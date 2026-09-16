import re

import ollama_patch as op

cp = op.cp
app = op.app
_original_generate_route = op._original_generate_route


def _plain_word_count(html):
    text = re.sub(r'<[^>]+>', ' ', str(html or ''))
    text = re.sub(r'&[a-zA-Z0-9#]+;', ' ', text)
    return len(re.findall(r"\b[\wÀ-ÿ’'-]+\b", text, flags=re.UNICODE))


def _source_word_count(text):
    return len(re.findall(r"\b[\wÀ-ÿ’'-]+\b", str(text or ''), flags=re.UNICODE))


def _minimum_article_words(source_text):
    words = _source_word_count(source_text)
    if words >= 1200: return 500
    if words >= 800: return 400
    if words >= 500: return 300
    if words >= 300: return 220
    if words >= 150: return 130
    return 70


def _quality_prompt(source_text, cfg, retry=False, minimum_words=0, previous_draft=''):
    base = op._article_prompt(source_text, cfg, retry=retry)
    instructions = f'''

REQUISITI DI COMPLETEZZA GIORNALISTICA:
- Non fare un semplice riassunto.
- La fonte contiene circa {_source_word_count(source_text)} parole: produci un articolo completo di almeno {minimum_words} parole, se le informazioni disponibili lo consentono.
- Utilizza una parte significativa della fonte e sviluppa la notizia in più paragrafi.
- Mantieni evento, data, luogo, orario, protagonisti, organizzatori, numeri, modalità di partecipazione, dichiarazioni e link utili presenti nella fonte.
- Distingui rigorosamente persone intervistate, relatori, organizzatori e persone semplicemente citate.
- Non inventare informazioni per raggiungere la lunghezza.
- L'articolo deve terminare con una frase completa.
'''
    if retry:
        draft = str(previous_draft or '').strip()
        if len(draft) > 6000:
            draft = draft[:6000]
        instructions += f'''

SECONDO TENTATIVO OBBLIGATORIO:
Il tentativo precedente è stato giudicato troppo breve o incompleto. Non limitarti a correggere l'ultima frase: riscrivi ed espandi l'intero articolo usando nuovamente tutta la fonte originale. Devi arrivare almeno a {minimum_words} parole quando la fonte lo consente.

BOZZA PRECEDENTE INSUFFICIENTE, DA ESPANDERE E COMPLETARE:
{draft}
'''
    return base + instructions


def generate_article_ollama_quality(source_text, cfg):
    base_url = (cfg.get('ollama_url') or '').strip().rstrip('/')
    model = (cfg.get('ollama_model') or '').strip()
    if not base_url: raise RuntimeError('Configura l’URL di Ollama nella dashboard.')
    if not model: raise RuntimeError('Configura il modello Ollama nella dashboard.')

    temperature = op._float_value(cfg.get('ollama_temperature'), 0.3, 0.0, 2.0)
    top_p = op._float_value(cfg.get('ollama_top_p'), 0.9, 0.0, 1.0)
    configured_tokens = op._int_value(cfg.get('ollama_max_tokens'), 16384, 512, 32768)
    first_tokens = max(configured_tokens, 16384)
    second_tokens = max(first_tokens, 32768)
    endpoint = base_url + '/api/generate'
    minimum_words = _minimum_article_words(source_text)
    last_reason = 'risposta non valida'
    previous_raw = ''

    cp.base.log(f'Ollama v2.3.4: fonte ricevuta dal generatore={_source_word_count(source_text)} parole; minimo articolo={minimum_words}.')

    for attempt in (1, 2):
        attempt_tokens = first_tokens if attempt == 1 else second_tokens
        try:
            result = op._ollama_request(endpoint, model,
                _quality_prompt(source_text, cfg, retry=(attempt == 2), minimum_words=minimum_words, previous_draft=previous_raw),
                temperature, top_p, attempt_tokens, structured=True)
        except Exception as exc:
            raise RuntimeError(f'Ollama non raggiungibile su {base_url}: {type(exc).__name__}: {exc}') from exc

        raw = str(result.get('response') or '').strip()
        done = result.get('done')
        done_reason = str(result.get('done_reason') or '').strip().lower()
        eval_count = result.get('eval_count', '?')
        prompt_eval_count = result.get('prompt_eval_count', '?')
        cp.base.log(f'Ollama qualità: tentativo {attempt}/2; done={done}; done_reason={done_reason or "n/d"}; prompt_eval_count={prompt_eval_count}; eval_count={eval_count}; num_predict={attempt_tokens}.')

        if done is False or done_reason in ('length', 'max_tokens', 'limit'):
            last_reason = f'risposta interrotta dal limite Ollama (done_reason={done_reason or "n/d"})'
            previous_raw = raw
            continue

        parsed = op._parse_ollama_output(raw)
        if not parsed:
            last_reason = 'formato della risposta non interpretabile'
            previous_raw = raw
            cp.base.log(f'Ollama qualità: output non interpretabile: {raw[:2000].replace(chr(10), " ")}')
            continue

        title, article = parsed
        article_words = _plain_word_count(article)
        # done_reason=stop è una terminazione normale. La completezza viene valutata soprattutto sulla quantità di contenuto.
        if article_words >= minimum_words:
            cp.base.log(f'Ollama qualità: articolo accettato al tentativo {attempt}/2 ({article_words} parole; minimo {minimum_words}; fonte {_source_word_count(source_text)} parole; done_reason={done_reason or "n/d"}).')
            return title, article

        last_reason = f'articolo troppo breve: {article_words} parole, minimo richiesto {minimum_words}'
        previous_raw = raw
        cp.base.log(f'Ollama qualità: {last_reason}. Il secondo tentativo riceverà anche la bozza precedente.' if attempt == 1 else f'Ollama qualità: {last_reason}.')

    raise RuntimeError(f'Ollama non ha prodotto un articolo sufficientemente completo dopo 2 tentativi ({last_reason}). Controlla il log applicazione.')


def generate_route_with_quality(index):
    from flask import request
    engine = (request.form.get('ai_engine') or 'default').strip().lower()
    if engine != 'ollama':
        return _original_generate_route(index)
    original_generator = cp.base.generate_article
    cp.base.generate_article = generate_article_ollama_quality
    try:
        cp.base.log(f'Generazione articolo con Ollama v2.3.4 richiesta per mail {index}')
        return _original_generate_route(index)
    finally:
        cp.base.generate_article = original_generator

app.view_functions['generate_route'] = generate_route_with_quality

RUNTIME_APP_VERSION = '2.3.4'
cp.APP_VERSION = RUNTIME_APP_VERSION
op.RUNTIME_APP_VERSION = RUNTIME_APP_VERSION

@app.context_processor
def inject_quality_patch_version():
    return {'app_version': RUNTIME_APP_VERSION}

cp.CHANGELOG.insert(0, {
    'version': '2.3.4', 'date': '16 settembre 2026', 'changes': [
        'Il controllo non considera più automaticamente troncato un articolo solo perché manca la punteggiatura finale quando Ollama termina con stop.',
        'Il secondo tentativo riceve anche la bozza precedente insufficiente e deve riscriverla ed espanderla usando tutta la fonte.',
        'Limite di generazione portato ad almeno 16384 token al primo tentativo e 32768 al secondo.',
        'Aggiunto log del numero di parole realmente ricevute dal generatore Ollama.',
        'Il runtime registra separatamente parole del corpo email, testo degli allegati e totale inviato alla AI.',
        'Corretto il recupero dopo errore AI che richiamava la funzione inesistente fixed_app.render_open_mail.',
    ]
})
