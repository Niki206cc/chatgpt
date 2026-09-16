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
    if words >= 1200:
        return 500
    if words >= 800:
        return 400
    if words >= 500:
        return 300
    if words >= 300:
        return 220
    if words >= 150:
        return 130
    return 70


def _looks_truncated(article):
    text = re.sub(r'<[^>]+>', ' ', str(article or '')).strip()
    text = re.sub(r'\s+', ' ', text)
    if not text:
        return True
    # Una chiusura con punteggiatura forte è normalmente un buon segnale.
    if re.search(r'[.!?…][\s\"”’)]*$', text):
        return False
    # Anche una chiusura HTML completa può essere valida se il testo termina con citazione.
    if re.search(r'</(?:p|ul|ol|blockquote)>\s*$', str(article or '').strip(), flags=re.I):
        return False
    return True


def _quality_prompt(source_text, cfg, retry=False, minimum_words=0):
    base = op._article_prompt(source_text, cfg, retry=retry)
    instructions = f'''

REQUISITI DI COMPLETEZZA GIORNALISTICA:
- Non fare un semplice riassunto del comunicato.
- La fonte contiene circa {_source_word_count(source_text)} parole: produci un articolo completo di almeno {minimum_words} parole, salvo che la fonte non contenga informazioni sufficienti.
- Se la fonte è lunga, utilizza una parte significativa delle informazioni disponibili e sviluppa la notizia in più paragrafi.
- Mantieni tutti gli elementi giornalisticamente rilevanti presenti nella fonte: evento, data, luogo, orario, protagonisti, organizzatori, numeri, modalità di partecipazione e link utili.
- Quando sono presenti dichiarazioni significative, includine almeno una riportandone fedelmente il senso; non attribuire mai una dichiarazione alla persona sbagliata.
- Distingui rigorosamente tra persone intervistate, relatori, organizzatori e persone semplicemente citate.
- Se la fonte descrive più sezioni, puntate, temi o fasi, sintetizzale senza ridurre l'intero articolo a poche righe.
- Non aggiungere fatti esterni alla fonte per raggiungere la lunghezza richiesta.
- Conserva i link pertinenti presenti nella fonte usando HTML <a href="URL" target="_blank" rel="noopener">testo descrittivo</a>.
- L'articolo deve terminare in modo completo: non interrompere mai una frase, un paragrafo o una citazione a metà.
'''
    if retry:
        instructions += f'''\nIl tentativo precedente era troppo breve, incompleto o troncato. Genera nuovamente l'intero articolo dall'inizio, raggiungi almeno {minimum_words} parole e assicurati che l'ultima frase sia completa.\n'''
    return base + instructions


def generate_article_ollama_quality(source_text, cfg):
    base_url = (cfg.get('ollama_url') or '').strip().rstrip('/')
    model = (cfg.get('ollama_model') or '').strip()
    if not base_url:
        raise RuntimeError('Configura l’URL di Ollama nella dashboard.')
    if not model:
        raise RuntimeError('Configura il modello Ollama nella dashboard.')

    temperature = op._float_value(cfg.get('ollama_temperature'), 0.3, 0.0, 2.0)
    top_p = op._float_value(cfg.get('ollama_top_p'), 0.9, 0.0, 1.0)
    configured_tokens = op._int_value(cfg.get('ollama_max_tokens'), 8192, 512, 16384)
    # Protezione aggiuntiva: per Ollama non scendere sotto 8192 token.
    first_tokens = max(configured_tokens, 8192)
    second_tokens = min(max(first_tokens * 2, 12288), 16384)
    endpoint = base_url + '/api/generate'
    minimum_words = _minimum_article_words(source_text)
    last_reason = 'risposta non valida'

    for attempt in (1, 2):
        attempt_tokens = first_tokens if attempt == 1 else second_tokens
        try:
            result = op._ollama_request(
                endpoint,
                model,
                _quality_prompt(source_text, cfg, retry=(attempt == 2), minimum_words=minimum_words),
                temperature,
                top_p,
                attempt_tokens,
                structured=True,
            )
        except Exception as exc:
            raise RuntimeError(f'Ollama non raggiungibile su {base_url}: {type(exc).__name__}: {exc}') from exc

        raw = str(result.get('response') or '').strip()
        done = result.get('done')
        done_reason = str(result.get('done_reason') or '').strip().lower()
        eval_count = result.get('eval_count', '?')
        prompt_eval_count = result.get('prompt_eval_count', '?')
        cp.base.log(
            f'Ollama qualità: tentativo {attempt}/2; done={done}; done_reason={done_reason or "n/d"}; '
            f'prompt_eval_count={prompt_eval_count}; eval_count={eval_count}; num_predict={attempt_tokens}.'
        )

        # Ollama usa normalmente stop quando la risposta è terminata correttamente.
        if done is False or done_reason in ('length', 'max_tokens', 'limit'):
            last_reason = f'risposta troncata da Ollama (done_reason={done_reason or "n/d"})'
            cp.base.log(f'Ollama qualità: {last_reason}.')
            continue

        parsed = op._parse_ollama_output(raw)
        if not parsed:
            last_reason = 'formato della risposta non interpretabile'
            cp.base.log(f'Ollama qualità: tentativo {attempt}/2 non interpretabile: {raw[:2000].replace(chr(10), " ")}')
            continue

        title, article = parsed
        article_words = _plain_word_count(article)
        if _looks_truncated(article):
            last_reason = 'ultima frase dell’articolo apparentemente troncata'
            cp.base.log(f'Ollama qualità: {last_reason}; {article_words} parole. Nuovo tentativo.' if attempt == 1 else f'Ollama qualità: {last_reason}.')
            continue

        if article_words >= minimum_words:
            cp.base.log(f'Ollama qualità: articolo accettato al tentativo {attempt}/2 ({article_words} parole; minimo {minimum_words}; fonte {_source_word_count(source_text)} parole).')
            return title, article

        last_reason = f'articolo troppo breve: {article_words} parole, minimo richiesto {minimum_words}'
        cp.base.log(f'Ollama qualità: {last_reason}. Avvio nuovo tentativo.' if attempt == 1 else f'Ollama qualità: {last_reason}.')

    raise RuntimeError(f'Ollama non ha prodotto un articolo completo dopo 2 tentativi ({last_reason}). Controlla il log applicazione.')


def generate_route_with_quality(index):
    from flask import request
    engine = (request.form.get('ai_engine') or 'default').strip().lower()
    if engine != 'ollama':
        return _original_generate_route(index)

    original_generator = cp.base.generate_article
    cp.base.generate_article = generate_article_ollama_quality
    try:
        cp.base.log(f'Generazione articolo con Ollama v2.3.3 richiesta per mail {index}')
        return _original_generate_route(index)
    finally:
        cp.base.generate_article = original_generator


app.view_functions['generate_route'] = generate_route_with_quality

RUNTIME_APP_VERSION = '2.3.3'
cp.APP_VERSION = RUNTIME_APP_VERSION
op.RUNTIME_APP_VERSION = RUNTIME_APP_VERSION


@app.context_processor
def inject_quality_patch_version():
    return {'app_version': RUNTIME_APP_VERSION}


cp.CHANGELOG.insert(0, {
    'version': '2.3.3',
    'date': '16 settembre 2026',
    'changes': [
        'Aggiunto controllo done e done_reason restituiti da Ollama per riconoscere le risposte interrotte dal limite di generazione.',
        'Gli articoli che terminano a metà frase vengono rifiutati automaticamente.',
        'Il primo tentativo Ollama dispone di almeno 8192 token; il secondo può salire automaticamente fino a 16384.',
        'Il secondo tentativo rigenera l’intero articolo quando il primo risulta troncato, troppo breve o incompleto.',
        'Il log mostra done_reason, prompt_eval_count, eval_count e limite token utilizzato.',
    ],
})
