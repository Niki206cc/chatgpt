import configparser
import json
from urllib.request import Request, urlopen

import runtime_patch as rp

cp = rp.cp
app = rp.app

_original_load_config = cp.base.load_config
_original_save_config = cp.base.save_config_from_form
_original_generate_route = rp.runtime_generate_route

DEFAULT_OLLAMA_PROMPT = '''Sei un giornalista professionista della redazione di Montagne & Paesi, testata online locale dedicata principalmente alle province di Bergamo, Brescia e Sondrio.

Devi trasformare comunicati stampa, email, documenti e informazioni fornite in articoli giornalistici pronti per essere pubblicati su WordPress.

OBIETTIVO
Scrivi un articolo originale, chiaro, autorevole e informativo. Non limitarti a riassumere il comunicato: riorganizza le informazioni secondo criteri giornalistici, mettendo subito in evidenza la notizia principale.

TITOLO
- Crea un titolo giornalistico efficace e naturale.
- Deve essere adatto a Google News, Google Discover e motori di ricerca.
- Inserisci località e argomento principale quando pertinenti.
- Evita titoli clickbait e maiuscole inutili.
- Non usare HTML, markdown o virgolette nel titolo.
- Non aggiungere informazioni non presenti nella fonte.

ARTICOLO WORDPRESS
- Restituisci HTML pulito pronto per WordPress.
- Usa <p> per i paragrafi.
- Usa <strong> solo per informazioni realmente importanti, nomi, località o concetti chiave.
- Puoi usare <ul> e <li> quando un elenco migliora realmente la leggibilità.
- Puoi usare <blockquote> per dichiarazioni particolarmente rilevanti.
- Non usare Markdown, <h1> o CSS inline.
- Evita <h2> e <h3> negli articoli brevi; usali solo negli articoli lunghi quando migliorano davvero la struttura.

LINK
- Se nella fonte è presente un URL utile e pertinente, mantienilo con <a href="URL">testo descrittivo</a>.
- Per link esterni puoi usare <a href="URL" target="_blank" rel="noopener">testo descrittivo</a>.
- Non inventare URL o link non presenti nella fonte.
- Evita URL grezzi quando è possibile usare un testo descrittivo.

SEO E DISCOVER
- Individua naturalmente la keyword principale della notizia.
- Inseriscila nel titolo quando appropriato, nel primo paragrafo e naturalmente nel corpo.
- Inserisci località, provincia e territorio quando presenti nella fonte.
- Usa sinonimi e termini semanticamente correlati senza keyword stuffing.
- Il primo paragrafo deve spiegare immediatamente chi, cosa, dove e quando.
- Ottimizza per Google Search, Google News, Google Discover e condivisione social privilegiando chiarezza e utilità.

STILE
- Stile giornalistico italiano, locale, chiaro e diretto.
- Frasi abbastanza brevi e paragrafi leggibili.
- Evita tono promozionale salvo quando necessario per descrivere fedelmente un'iniziativa.
- Elimina formule tipiche dei comunicati stampa non necessarie.
- Non iniziare con "Riceviamo e pubblichiamo".
- Non inserire conclusioni artificiali, firma o note sulla tua elaborazione.

ACCURATEZZA
- Non inventare mai nomi, dichiarazioni, date, numeri, luoghi, cariche o circostanze.
- Mantieni esattamente i dati presenti nella fonte.
- Se un'informazione non è disponibile, non dedurla.
- Le citazioni devono rispettare il significato originale.
- Non trasformare ipotesi o informazioni non confermate in fatti.

LOCALITA'
Presta particolare attenzione ai nomi dei Comuni, delle valli e delle province. Quando pertinente valorizza naturalmente il contesto territoriale di Bergamo, Brescia, Sondrio, Valle Seriana, Val Brembana, Val Gandino, Valle Camonica, Sebino, Franciacorta e Valtellina.

FORMATO DI RISPOSTA
Rispondi ESCLUSIVAMENTE in questo formato:

TITOLO:
[titolo]

ARTICOLO:
[HTML WordPress dell'articolo]'''


def _float_value(value, default, minimum, maximum):
    try:
        number = float(value)
        return max(minimum, min(maximum, number))
    except Exception:
        return default


def _int_value(value, default, minimum, maximum):
    try:
        number = int(value)
        return max(minimum, min(maximum, number))
    except Exception:
        return default


def load_config_with_ollama():
    cfg = _original_load_config()
    cfg.setdefault('ollama_url', 'http://192.168.1.72:11434')
    cfg.setdefault('ollama_model', 'qwen2.5vl:3b')
    cfg.setdefault('ollama_prompt', DEFAULT_OLLAMA_PROMPT)
    cfg.setdefault('ollama_temperature', '0.3')
    cfg.setdefault('ollama_top_p', '0.9')
    cfg.setdefault('ollama_max_tokens', '4096')
    try:
        if cp.base.CONFIG_FILE.exists():
            parser = configparser.ConfigParser(interpolation=None)
            parser.read(cp.base.CONFIG_FILE, encoding='utf-8')
            cfg['ollama_url'] = parser.get('OLLAMA', 'url', fallback=cfg['ollama_url']).strip().rstrip('/')
            cfg['ollama_model'] = parser.get('OLLAMA', 'model', fallback=cfg['ollama_model']).strip()
            cfg['ollama_prompt'] = parser.get('OLLAMA', 'prompt', fallback=cfg['ollama_prompt']).strip() or DEFAULT_OLLAMA_PROMPT
            cfg['ollama_temperature'] = parser.get('OLLAMA', 'temperature', fallback=cfg['ollama_temperature']).strip()
            cfg['ollama_top_p'] = parser.get('OLLAMA', 'top_p', fallback=cfg['ollama_top_p']).strip()
            cfg['ollama_max_tokens'] = parser.get('OLLAMA', 'max_tokens', fallback=cfg['ollama_max_tokens']).strip()
    except Exception as exc:
        cp.base.log_exception('Errore lettura configurazione Ollama', exc)
    return cfg


def save_config_with_ollama(form):
    _original_save_config(form)
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(cp.base.CONFIG_FILE, encoding='utf-8')
    parser['OLLAMA'] = {
        'url': (form.get('ollama_url', '') or 'http://192.168.1.72:11434').strip().rstrip('/'),
        'model': (form.get('ollama_model', '') or 'qwen2.5vl:3b').strip(),
        'prompt': (form.get('ollama_prompt', '') or DEFAULT_OLLAMA_PROMPT).strip(),
        'temperature': str(_float_value(form.get('ollama_temperature'), 0.3, 0.0, 2.0)),
        'top_p': str(_float_value(form.get('ollama_top_p'), 0.9, 0.0, 1.0)),
        'max_tokens': str(_int_value(form.get('ollama_max_tokens'), 4096, 512, 16384)),
    }
    with open(cp.base.CONFIG_FILE, 'w', encoding='utf-8') as handle:
        parser.write(handle)


cp.base.load_config = load_config_with_ollama
cp.base.save_config_from_form = save_config_with_ollama


def _article_prompt(source_text, cfg):
    editorial_prompt = (cfg.get('ollama_prompt') or DEFAULT_OLLAMA_PROMPT).strip()
    return f'''{editorial_prompt}

DATA REALE DI OGGI:
{cp.base.italian_today_string()}

TESTO DA TRASFORMARE:
{source_text}'''.strip()


def generate_article_ollama(source_text, cfg):
    base_url = (cfg.get('ollama_url') or '').strip().rstrip('/')
    model = (cfg.get('ollama_model') or '').strip()
    if not base_url:
        raise RuntimeError('Configura l’URL di Ollama nella dashboard.')
    if not model:
        raise RuntimeError('Configura il modello Ollama nella dashboard.')

    temperature = _float_value(cfg.get('ollama_temperature'), 0.3, 0.0, 2.0)
    top_p = _float_value(cfg.get('ollama_top_p'), 0.9, 0.0, 1.0)
    max_tokens = _int_value(cfg.get('ollama_max_tokens'), 4096, 512, 16384)

    endpoint = base_url + '/api/generate'
    payload = json.dumps({
        'model': model,
        'prompt': _article_prompt(source_text, cfg),
        'stream': False,
        'options': {
            'temperature': temperature,
            'top_p': top_p,
            'num_predict': max_tokens,
        },
    }).encode('utf-8')
    request = Request(endpoint, data=payload, headers={'Content-Type': 'application/json', 'Accept': 'application/json'}, method='POST')
    try:
        with urlopen(request, timeout=240) as response:
            result = json.loads(response.read().decode('utf-8', errors='replace'))
    except Exception as exc:
        raise RuntimeError(f'Ollama non raggiungibile su {base_url}: {type(exc).__name__}: {exc}') from exc

    output = str(result.get('response') or '').strip()
    if 'TITOLO:' not in output or 'ARTICOLO:' not in output:
        raise RuntimeError('Ollama non ha rispettato il formato TITOLO/ARTICOLO. Riprova.')
    after_title = output.split('TITOLO:', 1)[1]
    title_part, article_part = after_title.split('ARTICOLO:', 1)
    return cp.base.clean_title(title_part.strip()), article_part.strip()


def generate_route_with_engine(index):
    from flask import request
    engine = (request.form.get('ai_engine') or 'default').strip().lower()
    if engine != 'ollama':
        return _original_generate_route(index)

    original_generator = cp.base.generate_article
    cp.base.generate_article = generate_article_ollama
    try:
        cp.base.log(f'Generazione articolo con Ollama richiesta per mail {index}')
        return _original_generate_route(index)
    finally:
        cp.base.generate_article = original_generator


app.view_functions['generate_route'] = generate_route_with_engine


@app.post('/test-ollama')
def test_ollama_route():
    from flask import flash, redirect, url_for
    cfg = cp.base.load_config()
    base_url = (cfg.get('ollama_url') or '').strip().rstrip('/')
    try:
        with urlopen(Request(base_url + '/api/tags', headers={'Accept': 'application/json'}), timeout=8) as response:
            data = json.loads(response.read().decode('utf-8', errors='replace'))
        models = [m.get('name', '') for m in data.get('models', []) if isinstance(m, dict)]
        configured = cfg.get('ollama_model', '')
        if configured and configured in models:
            flash(f'Ollama raggiungibile. Modello {configured} disponibile.', 'success')
        else:
            detail = ', '.join(models[:8]) or 'nessun modello rilevato'
            flash(f'Ollama raggiungibile. Modelli disponibili: {detail}', 'warning')
    except Exception as exc:
        cp.base.log_exception('Test connessione Ollama fallito', exc)
        flash(f'Ollama non raggiungibile: {type(exc).__name__}: {exc}', 'danger')
    return redirect(url_for('index'))


RUNTIME_APP_VERSION = '2.3.0'
cp.APP_VERSION = RUNTIME_APP_VERSION
rp.RUNTIME_APP_VERSION = RUNTIME_APP_VERSION


@app.context_processor
def inject_ollama_patch_version():
    return {'app_version': RUNTIME_APP_VERSION}


cp.CHANGELOG.insert(0, {
    'version': '2.3.0',
    'date': '15 settembre 2026',
    'changes': [
        'Aggiunto prompt editoriale Ollama completamente modificabile dalla dashboard.',
        'Prompt predefinito ottimizzato per WordPress, HTML, link, SEO, Google News e Google Discover.',
        'Aggiunti i parametri Temperature, Top P e Max token per Ollama.',
        'Impostati valori editoriali predefiniti: temperature 0.3, top_p 0.9 e 4096 token.',
        'Versione runtime allineata alla release corrente.',
    ],
})
cp.CHANGELOG.insert(1, {
    'version': '2.2.0',
    'date': '15 settembre 2026',
    'changes': [
        'Selezione automatica di Carica immagine quando viene scelto un file manualmente.',
        'Aggiunto pulsante Genera con Ollama nella mail aperta come alternativa a Gemini/OpenAI.',
        'Aggiunti URL e modello Ollama nella configurazione persistente.',
        'Aggiunto test di raggiungibilità del server Ollama.',
    ],
})
