import configparser
import json
from urllib.parse import urljoin
from urllib.request import Request, urlopen

import runtime_patch as rp

cp = rp.cp
app = rp.app

_original_load_config = cp.base.load_config
_original_save_config = cp.base.save_config_from_form
_original_generate_route = rp.runtime_generate_route


def load_config_with_ollama():
    cfg = _original_load_config()
    cfg.setdefault('ollama_url', 'http://192.168.1.100:11434')
    cfg.setdefault('ollama_model', 'qwen2.5vl:3b')
    try:
        if cp.base.CONFIG_FILE.exists():
            parser = configparser.ConfigParser()
            parser.read(cp.base.CONFIG_FILE, encoding='utf-8')
            cfg['ollama_url'] = parser.get('OLLAMA', 'url', fallback=cfg['ollama_url']).strip().rstrip('/')
            cfg['ollama_model'] = parser.get('OLLAMA', 'model', fallback=cfg['ollama_model']).strip()
    except Exception as exc:
        cp.base.log_exception('Errore lettura configurazione Ollama', exc)
    return cfg


def save_config_with_ollama(form):
    _original_save_config(form)
    parser = configparser.ConfigParser()
    parser.read(cp.base.CONFIG_FILE, encoding='utf-8')
    parser['OLLAMA'] = {
        'url': (form.get('ollama_url', '') or 'http://192.168.1.100:11434').strip().rstrip('/'),
        'model': (form.get('ollama_model', '') or 'qwen2.5vl:3b').strip(),
    }
    with open(cp.base.CONFIG_FILE, 'w', encoding='utf-8') as handle:
        parser.write(handle)


cp.base.load_config = load_config_with_ollama
cp.base.save_config_from_form = save_config_with_ollama


def _article_prompt(source_text):
    return f'''Devi trasformare il testo seguente in un articolo giornalistico per il sito locale Montagne & Paesi.

REGOLE IMPORTANTI:
- Rispondi esattamente con questo formato:
TITOLO:
[titolo qui]

ARTICOLO:
[articolo qui]

- Il TITOLO deve essere solo testo semplice, senza HTML, senza virgolette, senza markdown.
- Il titolo deve contenere evento, luogo preciso e dettaglio chiave.
- Il corpo ARTICOLO deve essere in HTML semplice per WordPress.
- Usa <strong></strong> per il grassetto.
- Non usare h1, h2, h3.
- Non usare markdown.
- Non inventare informazioni.
- Mantieni nomi, luoghi, orari e numeri esattamente come nel testo originale.
- Stile giornalistico, locale, chiaro, diretto. Frasi brevi.
- Primo paragrafo breve: deve riassumere tutta la notizia.
- Ottimizza per SEO, Google Discover e social.
- Usa in modo naturale parole chiave locali quando pertinenti: incidente, oggi, Bergamo, Brescia, Valle Seriana, Valle Camonica.
- Non firmare l'articolo.

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

    endpoint = base_url + '/api/generate'
    payload = json.dumps({
        'model': model,
        'prompt': _article_prompt(source_text),
        'stream': False,
    }).encode('utf-8')
    request = Request(endpoint, data=payload, headers={'Content-Type': 'application/json', 'Accept': 'application/json'}, method='POST')
    try:
        with urlopen(request, timeout=180) as response:
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


RUNTIME_APP_VERSION = '2.2.0'
cp.APP_VERSION = RUNTIME_APP_VERSION


@app.context_processor
def inject_ollama_patch_version():
    return {'app_version': RUNTIME_APP_VERSION}


cp.CHANGELOG.insert(0, {
    'version': '2.2.0',
    'date': '15 settembre 2026',
    'changes': [
        'Selezione automatica di Carica immagine quando viene scelto un file manualmente.',
        'Aggiunto pulsante Genera con Ollama nella mail aperta come alternativa a Gemini/OpenAI.',
        'Aggiunti URL e modello Ollama nella configurazione persistente.',
        'Aggiunto test di raggiungibilità del server Ollama.',
    ],
})
