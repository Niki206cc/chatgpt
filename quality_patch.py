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


def _minimum_article_words(source_text):
    words = _source_word_count(source_text)
    if words >= 1200: return 450
    if words >= 800: return 350
    if words >= 500: return 280
    if words >= 300: return 220
    if words >= 150: return 130
    return 70


def _free_prompt(source_text, cfg, minimum_words, retry=False, previous_article='', retry_reason=''):
    editorial = (cfg.get('ollama_prompt') or op.DEFAULT_OLLAMA_PROMPT).strip()
    editorial = re.sub(r'FORMATO DI RISPOSTA.*$', '', editorial, flags=re.I | re.S).strip()
    retry_text = ''
    if retry:
        previous_article = str(previous_article or '')[:7000]
        retry_text = f'''\n\nSECONDO TENTATIVO OBBLIGATORIO. La bozza precedente è stata rifiutata: {retry_reason}. Riscrivi l'articolo completamente da zero usando la fonte originale. NON copiare o continuare frasi dalla bozza precedente. Evita qualsiasi ripetizione.\n\nBOZZA PRECEDENTE DA NON COPIARE:\n{previous_article}\n'''
    return f'''{editorial}

ISTRUZIONI DI OUTPUT PER OLLAMA:
Scrivi esclusivamente il titolo e il corpo dell'articolo:
TITOLO: titolo dell'articolo
ARTICOLO:
<p>primo paragrafo...</p>
<p>altri paragrafi...</p>

REGOLE OBBLIGATORIE:
- HTML WordPress semplice e valido. Usa soprattutto <p>, eventualmente <strong>, <h2>, <h3>, <ul>, <li>, <blockquote> e <a href="URL">.
- Ogni paragrafo deve essere chiuso correttamente con </p>. Non inventare tag come <pp> e non inserire testo dentro i tag di chiusura.
- Non ripetere mai la stessa frase, lo stesso paragrafo, lo stesso orario o la stessa informazione per aumentare la lunghezza.
- Se hai esaurito le informazioni della fonte, termina l'articolo: NON riempire lo spazio con ripetizioni.
- Non usare Markdown: niente **, ##, ---, ``` o [testo](URL).
- Non aggiungere SEO, keyword, località, stile, note, analisi, meta description, LINK o FONTI.
- Dopo l'ultimo paragrafo non scrivere altro.
- Correggi grammatica e sintassi italiane prima di terminare la risposta.

Obiettivo: almeno {minimum_words} parole SOLO se la fonte contiene abbastanza informazioni. La qualità e la non ripetizione hanno priorità sulla lunghezza. Mantieni accuratamente fatti, date, luoghi, persone, ruoli, numeri, dichiarazioni e link della fonte. Non inventare nulla.{retry_text}

DATA REALE DI OGGI:
{cp.base.italian_today_string()}

FONTE ORIGINALE COMPLETA:
{source_text}'''.strip()


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


def _parse_free_output(raw):
    text = str(raw or '').strip()
    text = re.sub(r'^```(?:html|text)?\s*', '', text, flags=re.I); text = re.sub(r'\s*```$', '', text)
    match = re.search(r'TITOLO\s*:\s*(.*?)\s*ARTICOLO\s*:\s*(.+)', text, flags=re.I | re.S)
    if not match: return None
    title = cp.base.clean_title(match.group(1).strip().replace('**','').replace('__',''))
    article = _clean_article_output(match.group(2))
    return (title, article) if title and article else None


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
    repeated = [(s,c) for s,c in counts.items() if c >= 2]
    if repeated:
        worst = max(repeated, key=lambda x: x[1])
        return f'frase ripetuta {worst[1]} volte: {worst[0][:120]}'

    # Controlla sequenze di 10 parole ripetute: intercetta loop anche quando cambia solo la punteggiatura.
    words = re.findall(r"[\wÀ-ÿ’'-]+", plain.lower(), flags=re.UNICODE)
    if len(words) >= 40:
        grams = Counter(tuple(words[i:i+10]) for i in range(len(words)-9))
        max_repeat = max(grams.values(), default=1)
        if max_repeat >= 3: return f'sequenza di testo ripetuta {max_repeat} volte'

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
    configured_tokens = op._int_value(cfg.get('ollama_max_tokens'), 8192, 1024, 8192); first_tokens = min(max(configured_tokens, 4096), 8192); second_tokens = 8192
    endpoint = base_url + '/api/generate'; minimum_words = _minimum_article_words(source_text); last_reason = 'risposta non valida'; previous_article = ''
    _job_update(job_id, f'Fonte preparata: {_source_word_count(source_text)} parole. Invio a Ollama...', 15)
    cp.base.log(f'Ollama v2.4.3: fonte={_source_word_count(source_text)} parole; minimo={minimum_words}; max output={first_tokens}/{second_tokens}; controllo ripetizioni+HTML attivo.')
    for attempt in (1,2):
        tokens = first_tokens if attempt == 1 else second_tokens
        _job_update(job_id, f'Tentativo {attempt}/2: Ollama sta elaborando la fonte (context 32K, output max {tokens} token)...', 25 if attempt == 1 else 65)
        try:
            result = op._ollama_request(endpoint, model, _free_prompt(source_text, cfg, minimum_words, attempt == 2, previous_article, last_reason), temperature, top_p, tokens, structured=False)
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
        if words < minimum_words:
            deficit = minimum_words - words
            tolerance = max(20, int(minimum_words * 0.10))
            if deficit <= tolerance:
                cp.base.log(f'Ollama qualità: articolo sotto il minimo di sole {deficit} parole ({words}/{minimum_words}); accettato entro tolleranza.')
                _job_update(job_id, f'Articolo di {words} parole: leggermente sotto l’obiettivo ma entro tolleranza, accettato.', 95)
            else:
                last_reason = f'articolo troppo breve: {words} parole, obiettivo {minimum_words}'; previous_article = article
                cp.base.log(f'Ollama qualità: {last_reason}.'); _job_update(job_id, last_reason + ('. Rigenerazione...' if attempt == 1 else ''), 60 if attempt == 1 else 95); continue
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
        cp.base.log(f'Generazione articolo con Ollama v2.4.3 richiesta per mail {index}')
        response = _original_generate_route(index)
        _job_update(job_id, 'Generazione completata. Apertura anteprima...', 100, 'done')
        return response
    except Exception as exc:
        _job_update(job_id, f'Generazione terminata con errore: {type(exc).__name__}: {exc}', 100, 'error')
        raise
    finally: cp.base.generate_article = original_generator

app.view_functions['generate_route'] = generate_route_with_quality
RUNTIME_APP_VERSION = '2.4.3'; cp.APP_VERSION = RUNTIME_APP_VERSION; op.RUNTIME_APP_VERSION = RUNTIME_APP_VERSION

@app.context_processor
def inject_quality_patch_version(): return {'app_version': RUNTIME_APP_VERSION}

cp.CHANGELOG.insert(0, {'version':'2.4.3','date':'19 settembre 2026','changes':[
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
