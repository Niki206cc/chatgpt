import re
import json
import time
import threading
import uuid
from pathlib import Path
from collections import Counter

from bs4 import BeautifulSoup
import ollama_patch as op

cp = op.cp
app = op.app
_original_generate_route = op._original_generate_route

_OLLAMA_JOB_DIR = Path(cp.base.DATA_DIR) / 'ollama_jobs'
_OLLAMA_JOB_DIR.mkdir(parents=True, exist_ok=True)
_OLLAMA_JOBS_LOCK = threading.Lock()

def _job_path(job_id):
    safe = re.sub(r'[^a-zA-Z0-9_-]', '', str(job_id or ''))
    return _OLLAMA_JOB_DIR / (safe + '.json') if safe else None

def _job_update(job_id, message, progress=None, state=None, redirect_url=None):
    path = _job_path(job_id)
    if not path: return
    with _OLLAMA_JOBS_LOCK:
        job = {'messages': [], 'progress': 0, 'state': 'running'}
        try:
            if path.exists(): job.update(json.loads(path.read_text(encoding='utf-8')))
        except Exception: pass
        stamp = time.strftime('%H:%M:%S')
        job['messages'] = list(job.get('messages') or [])[-79:] + [f'{stamp} · {message}']
        if progress is not None: job['progress'] = max(0, min(100, int(progress)))
        if state: job['state'] = state
        if redirect_url: job['redirect_url'] = redirect_url
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(job, ensure_ascii=False), encoding='utf-8')
        tmp.replace(path)

@app.get('/ollama-status/<job_id>')
def ollama_status_route(job_id):
    from flask import jsonify
    path = _job_path(job_id)
    job = {'messages':['In attesa di avvio...'], 'progress':0, 'state':'waiting'}
    try:
        if path and path.exists(): job.update(json.loads(path.read_text(encoding='utf-8')))
    except Exception: pass
    return jsonify(job)


def _plain_text(html):
    return BeautifulSoup(str(html or ''), 'html.parser').get_text(' ', strip=True)


def _plain_word_count(html):
    return len(re.findall(r"\b[\wÀ-ÿ’'-]+\b", _plain_text(html), flags=re.UNICODE))


def _source_word_count(text): return len(re.findall(r"\b[\wÀ-ÿ’'-]+\b", str(text or ''), flags=re.UNICODE))


def _article_length_target(source_text):
    words = _source_word_count(source_text)
    if words >= 1800: return '450-750'
    if words >= 1000: return '350-650'
    if words >= 600: return '300-550'
    if words >= 300: return '220-400'
    return '120-280'


def _free_prompt(source_text, cfg, length_target, retry=False, previous_article='', retry_reason=''):
    retry_text = ''
    if retry:
        retry_text = f"""
SECONDO TENTATIVO.
La bozza precedente non è stata accettata per questo motivo: {retry_reason}.
Riparti esclusivamente dalle FONTI qui sotto e scrivi un nuovo articolo da zero.
Non cercare di allungare il testo: meglio un articolo più breve e completo che frasi ripetute.
"""

    return f"""Sei un giornalista di Montagne & Paesi.

COMPITO
Usa ESCLUSIVAMENTE le informazioni presenti nelle FONTI fornite sotto.
Le FONTI comprendono il testo della mail e, quando presenti, i testi estratti dai documenti allegati.
Leggi tutto il materiale, elimina le duplicazioni tra mail e allegati e trasformalo in UN SOLO articolo giornalistico pronto per WordPress.

TITOLO
Scrivi un titolo SEO naturale e giornalistico. Inserisci luogo e notizia principale quando presenti nella fonte.
Non usare clickbait, virgolette inutili, HTML o Markdown. Non inventare informazioni.

ARTICOLO
- Apri con la notizia principale: chi, cosa, dove e quando.
- Usa tutti i fatti utili della mail e degli allegati, senza copiarli meccanicamente.
- Mantieni esatti nomi, date, orari, luoghi, numeri, cariche e dichiarazioni.
- Non aggiungere fatti, interpretazioni o dettagli assenti dalle fonti.
- Non ripetere la stessa informazione, frase o paragrafo.
- Se mail e allegato dicono la stessa cosa, riportala una volta sola.
- Non allungare artificialmente l'articolo.
- Lunghezza indicativa: {length_target} parole, ma termina prima se le informazioni sono finite.
- Scrivi in italiano corretto, stile giornalistico chiaro e diretto.
- HTML WordPress semplice: usa soprattutto <p>...</p>; <strong> solo quando utile.
- Non scrivere SEO, keyword, note, fonti, analisi o spiegazioni dopo l'articolo.
- Non usare Markdown.

FORMATO ESATTO:
Restituisci SOLO un oggetto JSON valido con questi due campi:
{"titolo":"Titolo SEO dell'articolo","articolo":"Testo completo dell'articolo in paragrafi di testo semplice"}
Non inserire blocchi markdown, commenti o testo prima/dopo il JSON.
Nel campo articolo NON usare HTML: scrivi solo testo semplice separando i paragrafi con righe vuote.

{retry_text}
DATA DI OGGI:
{cp.base.italian_today_string()}

FONTI COMPLETE (MAIL + TESTI ESTRATTI DAGLI ALLEGATI):
{source_text}""".strip()


def _clean_article_output(article):
    text = str(article or '').strip()
    text = re.sub(r'^\s*(?:```(?:html|text)?\s*)+', '', text, flags=re.I)
    text = re.sub(r'(?:\s*```)+\s*$', '', text)
    text = re.sub(r'^\s*(?:\*\*|__)+\s*', '', text)
    stop_patterns = [
        r'(?im)^\s*(?:---+\s*)?(?:\*\*|__)?\s*LINK\s*:\s*',
        r'(?im)^\s*(?:---+\s*)?(?:\*\*|__)?\s*SEO(?:\s+E\s+DISCOVER)?\s*:\s*',
        r'(?im)^\s*(?:---+\s*)?(?:\*\*|__)?\s*(?:KEYWORD|PAROLE\s+CHIAVE|LOCALIT[ÀA]|STILE|META\s+DESCRIPTION|NOTE|FONTI)\s*:\s*',
    ]
    cut = len(text)
    for pattern in stop_patterns:
        match = re.search(pattern, text)
        if match: cut = min(cut, match.start())
    text = text[:cut].strip()
    text = re.sub(r'(?m)^\s*---+\s*$', '', text).strip()
    return text.replace('**', '').replace('__', '').strip()


def _article_text_to_html(text):
    plain = str(text or '').strip()
    # Se il modello restituisce comunque HTML, recuperiamo il testo senza bocciarlo.
    if '<' in plain and '>' in plain:
        plain = BeautifulSoup(plain, 'html.parser').get_text('\n\n', strip=True)
    paragraphs = [re.sub(r'\s+', ' ', p).strip() for p in re.split(r'\n\s*\n+', plain) if p.strip()]
    if len(paragraphs) <= 1 and plain:
        # Fallback: divide sulle frasi solo per evitare un unico blocco enorme.
        sentences = re.split(r'(?<=[.!?])\s+', re.sub(r'\s+', ' ', plain))
        paragraphs = [' '.join(sentences[i:i+3]).strip() for i in range(0, len(sentences), 3) if sentences[i:i+3]]
    import html
    return '\n'.join(f'<p>{html.escape(p, quote=False)}</p>' for p in paragraphs if p)


def _parse_free_output(raw):
    text = str(raw or '').strip()
    text = re.sub(r'^\x60\x60\x60(?:json|text)?\s*', '', text, flags=re.I)
    text = re.sub(r'\s*\x60\x60\x60$', '', text)
    # Percorso principale: JSON. Così il modello non deve generare HTML corretto.
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            title = cp.base.clean_title(str(data.get('titolo') or data.get('title') or '').strip())
            article_text = str(data.get('articolo') or data.get('article') or '').strip()
            if title and article_text:
                return title, _article_text_to_html(article_text)
    except Exception:
        pass
    # Recupero tollerante se Ollama aggiunge testo attorno al JSON.
    match_json = re.search(r'\{.*\}', text, flags=re.S)
    if match_json:
        try:
            data = json.loads(match_json.group(0))
            title = cp.base.clean_title(str(data.get('titolo') or data.get('title') or '').strip())
            article_text = str(data.get('articolo') or data.get('article') or '').strip()
            if title and article_text:
                return title, _article_text_to_html(article_text)
        except Exception:
            pass
    # Compatibilità con vecchio TITOLO / ARTICOLO.
    match = re.search(r'TITOLO\s*:\s*(.*?)\s*ARTICOLO\s*:\s*(.+)', text, flags=re.I | re.S)
    if match:
        title = cp.base.clean_title(match.group(1).strip())
        article_text = _clean_article_output(match.group(2))
        if title and article_text:
            return title, _article_text_to_html(article_text)
    return None


def _normalize_sentence(sentence):
    sentence = re.sub(r'<[^>]+>', ' ', sentence)
    sentence = re.sub(r'\s+', ' ', sentence).strip().lower()
    return sentence


def _quality_issue(article, source_text):
    raw = str(article or '')
    # Pattern HTML che indicano output corrotto prima che BeautifulSoup possa correggerli automaticamente.
    if re.search(r'<\s*pp(?:\s|>)', raw, flags=re.I): return 'HTML non valido: trovato tag <pp>'
    if re.search(r'</\s*p\s+[^>]+>', raw, flags=re.I): return 'HTML non valido: tag </p> corrotto'
    if raw.count('<') != raw.count('>'): return 'HTML non valido: parentesi dei tag non bilanciate'
    allowed = {'p','strong','em','h2','h3','ul','ol','li','blockquote','a','br'}
    soup = BeautifulSoup(raw, 'html.parser')
    bad_tags = sorted({tag.name for tag in soup.find_all(True) if tag.name not in allowed})
    if bad_tags: return 'HTML con tag non consentiti: ' + ', '.join(bad_tags)

    plain = _plain_text(raw)
    sentences = [_normalize_sentence(s) for s in re.split(r'(?<=[.!?])\s+', plain)]
    sentences = [s for s in sentences if len(s.split()) >= 8]
    counts = Counter(sentences)
    repeated = [(s,c) for s,c in counts.items() if c >= 3]
    if repeated:
        worst = max(repeated, key=lambda x: x[1])
        return f'frase ripetuta {worst[1]} volte: {worst[0][:120]}'

    # Controlla sequenze di 10 parole ripetute: intercetta loop anche quando cambia solo la punteggiatura.
    words = re.findall(r"[\wÀ-ÿ’'-]+", plain.lower(), flags=re.UNICODE)
    if len(words) >= 40:
        grams = Counter(tuple(words[i:i+10]) for i in range(len(words)-9))
        max_repeat = max(grams.values(), default=1)
        if max_repeat >= 4: return f'sequenza di testo ripetuta {max_repeat} volte'

    source_words = max(_source_word_count(source_text), 1)
    article_words = len(words)
    if article_words > max(1800, int(source_words * 1.35)):
        return f'articolo sproporzionatamente lungo: {article_words} parole su fonte di {source_words}'
    return ''


def generate_article_ollama_quality(source_text, cfg, job_id=None):
    base_url = (cfg.get('ollama_url') or '').strip().rstrip('/'); model = (cfg.get('ollama_model') or '').strip()
    if not base_url: raise RuntimeError('Configura l’URL di Ollama nella dashboard.')
    if not model: raise RuntimeError('Configura il modello Ollama nella dashboard.')
    temperature = op._float_value(cfg.get('ollama_temperature'), 0.3, 0.0, 2.0); top_p = op._float_value(cfg.get('ollama_top_p'), 0.9, 0.0, 1.0)
    configured_tokens = op._int_value(cfg.get('ollama_max_tokens'), 4096, 1024, 4096); first_tokens = min(max(configured_tokens, 3072), 4096); second_tokens = 4096
    endpoint = base_url + '/api/generate'; length_target = _article_length_target(source_text); last_reason = 'risposta non valida'; previous_article = ''
    _job_update(job_id, f'Fonte preparata: {_source_word_count(source_text)} parole. Invio a Ollama...', 15)
    cp.base.log(f'Ollama v2.5.1: fonte={_source_word_count(source_text)} parole; lunghezza indicativa={length_target}; max output={first_tokens}/{second_tokens}; prompt editoriale semplificato.')
    for attempt in (1,2):
        tokens = first_tokens if attempt == 1 else second_tokens
        _job_update(job_id, f'Tentativo {attempt}/2: Ollama sta elaborando la fonte (context 32K, output max {tokens} token)...', 25 if attempt == 1 else 65)
        try:
            result = op._ollama_request(endpoint, model, _free_prompt(source_text, cfg, length_target, attempt == 2, '', last_reason), temperature, top_p, tokens, structured=True)
        except TimeoutError as exc:
            _job_update(job_id, 'Timeout: Ollama non ha completato la generazione entro 600 secondi.', 100, 'error')
            raise RuntimeError('Ollama ha impiegato più di 600 secondi per completare la generazione.') from exc
        except Exception as exc:
            _job_update(job_id, f'Errore di comunicazione con Ollama: {type(exc).__name__}: {exc}', 100, 'error')
            raise RuntimeError(f'Errore di comunicazione con Ollama su {base_url}: {type(exc).__name__}: {exc}') from exc
        _job_update(job_id, f'Risposta ricevuta da Ollama al tentativo {attempt}. Controllo formato e qualità...', 55 if attempt == 1 else 85)
        raw = str(result.get('response') or '').strip(); done = result.get('done'); done_reason = str(result.get('done_reason') or '').strip().lower()
        cp.base.log(f'Ollama qualità: tentativo {attempt}/2; done={done}; done_reason={done_reason or "n/d"}; eval_count={result.get("eval_count","?")}; num_predict={tokens}.')
        if done is False or done_reason in ('length','max_tokens','limit'):
            last_reason = f'risposta interrotta dal limite Ollama ({done_reason or "n/d"})'; previous_article = raw; continue
        parsed = _parse_free_output(raw)
        if not parsed:
            last_reason = 'formato TITOLO/ARTICOLO non riconosciuto'; previous_article = raw; continue
        title, article = parsed; words = _plain_word_count(article)
        issue = _quality_issue(article, source_text)
        if issue:
            last_reason = issue; previous_article = article
            cp.base.log(f'Ollama qualità: bozza rifiutata al tentativo {attempt}/2: {issue}.'); _job_update(job_id, f'Bozza rifiutata: {issue}. Avvio rigenerazione.' if attempt == 1 else f'Bozza rifiutata: {issue}.', 60 if attempt == 1 else 95)
            continue
        # Nessun minimo editoriale rigido: un articolo conciso ma completo è valido.
        # Blocchiamo solo risposte chiaramente monche rispetto a una fonte sostanziosa.
        source_words = _source_word_count(source_text)
        hard_floor = 120 if source_words >= 500 else 70
        if words < hard_floor:
            last_reason = f'articolo chiaramente incompleto: {words} parole'
            cp.base.log(f'Ollama qualità: {last_reason}.')
            _job_update(job_id, last_reason + ('. Rigenerazione da zero...' if attempt == 1 else ''), 60 if attempt == 1 else 95)
            continue
        _job_update(job_id, f'Articolo verificato: {words} parole, HTML valido e nessuna ripetizione.', 95)
        cp.base.log(f'Ollama qualità: articolo accettato al tentativo {attempt}/2 ({words} parole), HTML e ripetizioni verificati.')
        return title, article
    raise RuntimeError(f'Ollama non ha prodotto un articolo pubblicabile dopo 2 tentativi ({last_reason}). Controlla il log applicazione.')


def generate_route_with_quality(index):
    from flask import request, jsonify, url_for
    engine = (request.form.get('ai_engine') or 'default').strip().lower()
    if engine != 'ollama': return _original_generate_route(index)
    job_id = (request.form.get('ollama_job_id') or '').strip()
    original_generator = cp.base.generate_article
    cp.base.generate_article = lambda source_text, cfg: generate_article_ollama_quality(source_text, cfg, job_id)
    try:
        _job_update(job_id, 'Richiesta ricevuta dal programma. Preparazione comunicato e allegati...', 5)
        cp.base.log(f'Generazione articolo con Ollama v2.5.1 richiesta per mail {index}')
        response = _original_generate_route(index)
        _job_update(job_id, 'Generazione completata. Apertura anteprima...', 100, 'done')
        return response
    except Exception as exc:
        _job_update(job_id, f'Generazione terminata con errore: {type(exc).__name__}: {exc}', 100, 'error')
        raise
    finally: cp.base.generate_article = original_generator

app.view_functions['generate_route'] = generate_route_with_quality
RUNTIME_APP_VERSION = '2.5.1'; cp.APP_VERSION = RUNTIME_APP_VERSION; op.RUNTIME_APP_VERSION = RUNTIME_APP_VERSION

@app.context_processor
def inject_quality_patch_version(): return {'app_version': RUNTIME_APP_VERSION}

cp.CHANGELOG.insert(0, {'version':'2.5.1','date':'29 settembre 2026','changes':[
    'Ollama ora restituisce JSON strutturato con titolo e articolo: eliminati gli errori dovuti al formato TITOLO/ARTICOLO.',
    'Ollama scrive il corpo in testo semplice; l’HTML WordPress viene creato dal programma, eliminando gli errori di tag sbilanciati.',
    'Parser tollerante mantiene compatibilità con le vecchie risposte TITOLO/ARTICOLO.',
    'Prompt Ollama riscritto e semplificato: usa esplicitamente testo mail e testo estratto dagli allegati per creare un unico articolo.',
    'Titolo richiesto in forma SEO naturale e articolo WordPress basato esclusivamente sulle fonti.',
    'Eliminato il minimo rigido di parole: gli articoli concisi ma completi non vengono più scartati.',
    'Secondo tentativo sempre da zero: la bozza difettosa non viene più reinserita nel prompt.',
    'Controllo anti-loop meno aggressivo sulle ripetizioni occasionali ma mantiene il blocco sui veri loop.',
    'Output massimo ridotto a 4096 token per diminuire tempi e rischio di degenerazione del modello.',
    'Corretto elenco mail obsoleto dopo cancellazione o nuova scansione: Gunicorn usa un solo worker con 4 thread, mantenendo una MAIL_CACHE unica.',
    'Il monitor Ollama continua ad aggiornarsi durante la generazione grazie ai thread concorrenti.',
    'Controllo lunghezza reso elastico: una bozza valida non viene più scartata per pochi vocaboli sotto l’obiettivo.',
    'Obiettivi di lunghezza Ollama leggermente ridotti per evitare rigenerazioni inutili e ripetizioni.',
    'Monitor Ollama corretto: stato condiviso su /data così il browser può leggerlo mentre un altro worker esegue la generazione.',
    'Context Ollama fissato a 32K (32768 token).',
    'Timeout della generazione Ollama aumentato da 240 a 600 secondi.',
    'Aggiunto monitor di avanzamento nella schermata della mail con messaggi sulle fasi di comunicazione e controllo qualità.',
    'Gestione multi-immagine: scelta separata dell’immagine in evidenza e delle foto aggiuntive da inserire in fondo all’articolo.',
    'Le immagini secondarie vengono inviate inline nell’HTML per Postie e non duplicano la foto in evidenza.',
    'Rilevamento automatico di frasi e sequenze di parole ripetute: le bozze in loop vengono rifiutate.',
    'Validazione dell’HTML e rifiuto di tag corrotti come <pp> o chiusure </p> malformate.',
    'Controllo contro articoli sproporzionatamente lunghi rispetto alla fonte.',
    'Secondo tentativo istruito a riscrivere da zero senza copiare la bozza difettosa.',
    'Output Ollama limitato a massimo 8192 token: più che sufficiente per gli articoli e meno incline a loop molto lunghi.',
]})
