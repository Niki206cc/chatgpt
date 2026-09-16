import re

import ollama_patch as op

cp = op.cp
app = op.app
_original_generate_route = op._original_generate_route


def _plain_word_count(html):
    text = re.sub(r'<[^>]+>', ' ', str(html or '')); text = re.sub(r'&[a-zA-Z0-9#]+;', ' ', text)
    return len(re.findall(r"\b[\wÀ-ÿ’'-]+\b", text, flags=re.UNICODE))


def _source_word_count(text): return len(re.findall(r"\b[\wÀ-ÿ’'-]+\b", str(text or ''), flags=re.UNICODE))


def _minimum_article_words(source_text):
    words = _source_word_count(source_text)
    if words >= 1200: return 500
    if words >= 800: return 400
    if words >= 500: return 300
    if words >= 300: return 220
    if words >= 150: return 130
    return 70


def _free_prompt(source_text, cfg, minimum_words, retry=False, previous_article=''):
    editorial = (cfg.get('ollama_prompt') or op.DEFAULT_OLLAMA_PROMPT).strip()
    # Rimuove l'istruzione JSON/strutturata dal prompt configurabile: con modelli piccoli può far collassare la risposta.
    editorial = re.sub(r'FORMATO DI RISPOSTA.*$', '', editorial, flags=re.I | re.S).strip()
    retry_text = ''
    if retry:
        previous_article = str(previous_article or '')[:8000]
        retry_text = f'''\n\nSECONDO TENTATIVO: la bozza precedente era troppo breve. Riscrivi l'articolo dall'inizio, molto più completo, usando tutta la fonte. Non limitarti a completare la bozza.\nBOZZA PRECEDENTE:\n{previous_article}\n'''
    return f'''{editorial}

ISTRUZIONI DI OUTPUT PER OLLAMA:
Scrivi direttamente il risultato nel seguente formato testuale, senza JSON e senza blocchi Markdown:
TITOLO: titolo dell'articolo
ARTICOLO:
<p>primo paragrafo...</p>
<p>altri paragrafi...</p>

L'articolo deve avere almeno {minimum_words} parole quando la fonte lo consente. Non fare un semplice riassunto. Usa una parte significativa delle informazioni della fonte. Mantieni accuratamente date, luoghi, persone, ruoli, numeri, dichiarazioni e link presenti. Non inventare nulla. L'articolo deve essere completo e terminare con una frase completa.{retry_text}

DATA REALE DI OGGI:
{cp.base.italian_today_string()}

FONTE ORIGINALE COMPLETA:
{source_text}'''.strip()


def _parse_free_output(raw):
    text = str(raw or '').strip()
    text = re.sub(r'^```(?:html|text)?\s*', '', text, flags=re.I); text = re.sub(r'\s*```$', '', text)
    match = re.search(r'TITOLO\s*:\s*(.*?)\s*ARTICOLO\s*:\s*(.+)', text, flags=re.I | re.S)
    if match:
        title = cp.base.clean_title(match.group(1).strip()); article = match.group(2).strip()
        if title and article: return title, article
    return None


def generate_article_ollama_quality(source_text, cfg):
    base_url = (cfg.get('ollama_url') or '').strip().rstrip('/'); model = (cfg.get('ollama_model') or '').strip()
    if not base_url: raise RuntimeError('Configura l’URL di Ollama nella dashboard.')
    if not model: raise RuntimeError('Configura il modello Ollama nella dashboard.')
    temperature = op._float_value(cfg.get('ollama_temperature'), 0.3, 0.0, 2.0); top_p = op._float_value(cfg.get('ollama_top_p'), 0.9, 0.0, 1.0)
    configured_tokens = op._int_value(cfg.get('ollama_max_tokens'), 16384, 512, 32768); first_tokens = max(configured_tokens, 16384); second_tokens = 32768
    endpoint = base_url + '/api/generate'; minimum_words = _minimum_article_words(source_text); last_reason = 'risposta non valida'; previous_article = ''
    cp.base.log(f'Ollama v2.3.5: fonte ricevuta={_source_word_count(source_text)} parole; minimo articolo={minimum_words}; modalità=HTML libero senza structured JSON.')
    for attempt in (1,2):
        tokens = first_tokens if attempt == 1 else second_tokens
        try:
            result = op._ollama_request(endpoint, model, _free_prompt(source_text, cfg, minimum_words, attempt == 2, previous_article), temperature, top_p, tokens, structured=False)
        except Exception as exc: raise RuntimeError(f'Ollama non raggiungibile su {base_url}: {type(exc).__name__}: {exc}') from exc
        raw = str(result.get('response') or '').strip(); done = result.get('done'); done_reason = str(result.get('done_reason') or '').strip().lower()
        cp.base.log(f'Ollama qualità: tentativo {attempt}/2; done={done}; done_reason={done_reason or "n/d"}; prompt_eval_count={result.get("prompt_eval_count","?")}; eval_count={result.get("eval_count","?")}; num_predict={tokens}; structured=False.')
        if done is False or done_reason in ('length','max_tokens','limit'):
            last_reason = f'risposta interrotta dal limite Ollama ({done_reason or "n/d"})'; previous_article = raw; continue
        parsed = _parse_free_output(raw)
        if not parsed:
            last_reason = 'formato TITOLO/ARTICOLO non riconosciuto'; previous_article = raw
            cp.base.log(f'Ollama qualità: formato libero non riconosciuto: {raw[:1500].replace(chr(10), " ")}'); continue
        title, article = parsed; words = _plain_word_count(article)
        if words >= minimum_words:
            cp.base.log(f'Ollama qualità: articolo accettato al tentativo {attempt}/2 ({words} parole; minimo {minimum_words}; fonte {_source_word_count(source_text)}).'); return title, article
        last_reason = f'articolo troppo breve: {words} parole, minimo richiesto {minimum_words}'; previous_article = article
        cp.base.log(f'Ollama qualità: {last_reason}. Nuovo tentativo in modalità libera.' if attempt == 1 else f'Ollama qualità: {last_reason}.')
    raise RuntimeError(f'Ollama non ha prodotto un articolo sufficientemente completo dopo 2 tentativi ({last_reason}). Controlla il log applicazione.')


def generate_route_with_quality(index):
    from flask import request
    engine = (request.form.get('ai_engine') or 'default').strip().lower()
    if engine != 'ollama': return _original_generate_route(index)
    original_generator = cp.base.generate_article; cp.base.generate_article = generate_article_ollama_quality
    try:
        cp.base.log(f'Generazione articolo con Ollama v2.3.5 richiesta per mail {index}'); return _original_generate_route(index)
    finally: cp.base.generate_article = original_generator

app.view_functions['generate_route'] = generate_route_with_quality
RUNTIME_APP_VERSION = '2.3.5'; cp.APP_VERSION = RUNTIME_APP_VERSION; op.RUNTIME_APP_VERSION = RUNTIME_APP_VERSION

@app.context_processor
def inject_quality_patch_version(): return {'app_version': RUNTIME_APP_VERSION}

cp.CHANGELOG.insert(0, {'version':'2.3.5','date':'16 settembre 2026','changes':[
    'Ollama genera ora titolo e articolo in formato testuale/HTML libero invece del constrained structured JSON, più affidabile con qwen2.5vl:3b.',
    'Restano il controllo di lunghezza e il secondo tentativo automatico con la bozza precedente.',
    'Disponibili fino a 16384 token al primo tentativo e 32768 al secondo.',
    'Corretta la gestione dell’immagine casuale: l’upload virtuale supporta read, seek e save.',
]})
