import json
import random
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

import confirmation_patch as cp

app = cp.app

RUNTIME_APP_VERSION = '2.3.4'
RANDOM_IMAGE_LIBRARY_URL = 'https://www.montagneepaesi.com/random_images/'
RANDOM_IMAGE_LIST_URL = urljoin(RANDOM_IMAGE_LIBRARY_URL, 'images.php')
RANDOM_IMAGE_EXTENSIONS = ('.jpg', '.jpeg', '.png', '.webp')
RANDOM_IMAGE_HISTORY_FILE = cp.base.DATA_DIR / 'random_image_history.json'
RANDOM_IMAGE_HISTORY_LIMIT = 30

# Correzione categoria Valle Seriana.
cp.CATEGORY_NAME_BY_VALUE['valseriana'] = 'Valle Seriana'
cp.APP_VERSION = RUNTIME_APP_VERSION


def _request(url):
    return Request(url, headers={
        'User-Agent': 'Mozilla/5.0 MontagnePaesiArticleAutomation/2.3',
        'Accept': 'application/json,text/plain,*/*',
    })


def _load_random_history():
    try:
        if RANDOM_IMAGE_HISTORY_FILE.exists():
            data = json.loads(RANDOM_IMAGE_HISTORY_FILE.read_text(encoding='utf-8'))
            if isinstance(data, list):
                return [str(item) for item in data][-RANDOM_IMAGE_HISTORY_LIMIT:]
    except Exception as exc:
        cp.base.log_exception('Errore lettura storico immagini casuali', exc)
    return []


def _save_random_history(history):
    try:
        cp.base.DATA_DIR.mkdir(parents=True, exist_ok=True)
        RANDOM_IMAGE_HISTORY_FILE.write_text(
            json.dumps(history[-RANDOM_IMAGE_HISTORY_LIMIT:], ensure_ascii=False, indent=2),
            encoding='utf-8',
        )
    except Exception as exc:
        cp.base.log_exception('Errore salvataggio storico immagini casuali', exc)


def remember_random_image(url):
    if not url:
        return
    history = _load_random_history()
    history = [item for item in history if item != url]
    history.append(url)
    _save_random_history(history)


def fetch_random_image_urls():
    with urlopen(_request(RANDOM_IMAGE_LIST_URL), timeout=20) as response:
        raw = response.read().decode('utf-8', errors='replace')
    data = json.loads(raw)
    if isinstance(data, dict):
        data = data.get('images') or data.get('files') or []
    if not isinstance(data, list):
        raise RuntimeError('La libreria immagini non ha restituito una lista JSON valida.')

    urls = []
    for item in data:
        if isinstance(item, dict):
            item = item.get('url') or item.get('file') or item.get('name')
        if not isinstance(item, str):
            continue
        item = item.strip()
        if not item or not item.lower().endswith(RANDOM_IMAGE_EXTENSIONS):
            continue
        if item.startswith('http://') or item.startswith('https://'):
            url = item
        else:
            url = urljoin(RANDOM_IMAGE_LIBRARY_URL, item.lstrip('/'))
        if url not in urls:
            urls.append(url)
    return urls


def choose_random_image(current=''):
    urls = fetch_random_image_urls()
    if not urls:
        raise RuntimeError('Nessuna immagine disponibile nella libreria casuale.')
    history = set(_load_random_history())
    candidates = [url for url in urls if url not in history and url != current]
    if not candidates:
        candidates = [url for url in urls if url != current] or urls
    return random.choice(candidates)


def _valid_random_image_url(url):
    try:
        candidate = urlparse(url)
        base = urlparse(RANDOM_IMAGE_LIBRARY_URL)
        return (
            candidate.scheme in ('http', 'https')
            and candidate.netloc == base.netloc
            and candidate.path.startswith(base.path)
            and candidate.path.lower().endswith(RANDOM_IMAGE_EXTENSIONS)
        )
    except Exception:
        return False


def download_random_image(url):
    if not _valid_random_image_url(url):
        raise RuntimeError('URL immagine casuale non valido.')
    cp.base.ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix not in RANDOM_IMAGE_EXTENSIONS:
        raise RuntimeError('Formato immagine casuale non supportato.')
    with urlopen(_request(url), timeout=30) as response:
        data = response.read()
        content_type = (response.headers.get('Content-Type') or '').lower()
    if not data or len(data) < 1024:
        raise RuntimeError('L’immagine casuale scaricata è vuota o troppo piccola.')
    if content_type and not content_type.startswith('image/'):
        raise RuntimeError(f'Il file casuale non risulta un’immagine: {content_type}')
    filename = f'immagine-random-{int(time.time())}{suffix}'
    path = cp.base.ATTACHMENTS_DIR / filename
    path.write_bytes(data)
    cp.base.log(f'Immagine casuale scaricata: {url} -> {filename} ({len(data)} byte)')
    return path


def _word_count(text):
    return len(re.findall(r"\b[\wÀ-ÿ’'-]+\b", str(text or ''), flags=re.UNICODE))


def runtime_generate_route(index):
    from flask import flash, redirect, render_template, url_for
    from bs4 import BeautifulSoup
    cfg = cp.base.load_config()
    item = None
    try:
        item = cp.fixed.find_item(index, cfg)
        msg = cp.base.fetch_full_message_for_item(item, cfg)
        original_subject, sender, date = cp.base.get_email_preview(msg)
        email_body = cp.base.extract_body_from_email(msg)
        attachments, images, documents_text = cp.base.save_attachments_and_extract_text(msg)
        source_text = f'''OGGETTO EMAIL:\n{original_subject}\n\nMITTENTE:\n{sender}\n\nDATA EMAIL:\n{date}\n\nTESTO DELLA MAIL:\n{email_body}\n\n{documents_text}'''.strip()
        document_files = [p for p in attachments if Path(p).suffix.lower() in cp.base.DOCUMENT_EXTENSIONS]
        cp.base.log(
            f'Fonte preparata per AI: corpo email={_word_count(email_body)} parole; '
            f'documenti={len(document_files)}; testo allegati={_word_count(documents_text)} parole; '
            f'totale inviato={_word_count(source_text)} parole.'
        )
        if document_files and not documents_text.strip():
            raise RuntimeError('Sono presenti documenti allegati ma non è stato estratto alcun testo. Generazione bloccata per evitare una fonte incompleta.')
        if len(source_text) < 100:
            flash('Testo insufficiente per generare un articolo.', 'warning')
            return redirect(url_for('view_mail', index=item.server_id))
        article_title, html_article = cp.base.generate_article(source_text, cfg)
        article_title = cp.normalize_generated_title(article_title, source_text)
        sender_email = cp.base.extract_sender_email(msg)
        editorial = cp.editorial_images(images)
        has_editorial_images = bool(editorial)
        image_names = [p.name for p in editorial]
        selected_image = cp.fixed.largest_image_name(editorial) if editorial else ''
        default_image_available = False
        try:
            default_image = cp.ensure_default_image()
            default_image_available = True
            if default_image.name not in image_names:
                image_names.append(default_image.name)
        except Exception as default_exc:
            cp.base.log_exception('Errore preparazione immagine predefinita', default_exc)
        random_image_url = ''
        try:
            random_image_url = choose_random_image()
        except Exception as random_exc:
            cp.base.log_exception('Libreria immagini casuali non disponibile', random_exc)
        article_text = BeautifulSoup(html_article, 'html.parser').get_text(' ', strip=True)
        image_prompt = f'Generami un’immagine per questo articolo: {article_title}. {article_text}'
        return render_template('preview.html', title=cp.base.APP_TITLE, index=item.server_id,
            article_title=article_title, html_article=html_article, image_names=image_names,
            selected_image=selected_image, sender_email=sender_email,
            has_editorial_images=has_editorial_images, default_image_filename=cp.DEFAULT_IMAGE_FILENAME,
            default_image_available=default_image_available, image_prompt=image_prompt,
            random_image_url=random_image_url, random_library_url=RANDOM_IMAGE_LIBRARY_URL)
    except Exception as exc:
        cp.base.log_exception('Errore generazione articolo', exc)
        flash(f'Errore generazione articolo: {type(exc).__name__}: {exc}', 'danger')
        # Non richiamare fixed_app.render_open_mail: la funzione non esiste.
        target = item.server_id if item is not None else index
        return redirect(url_for('view_mail', index=target))


def random_image_route():
    from flask import jsonify, request
    try:
        current = request.args.get('current', '').strip()
        url = choose_random_image(current)
        return jsonify({'ok': True, 'url': url})
    except Exception as exc:
        cp.base.log_exception('Errore scelta immagine casuale', exc)
        return jsonify({'ok': False, 'error': str(exc)}), 500


app.view_functions['generate_route'] = runtime_generate_route
app.add_url_rule('/random-image', endpoint='random_image_route', view_func=random_image_route, methods=['GET'])


_original_send_article = cp.send_article_route

def runtime_send_article(index):
    from flask import request
    if request.form.get('image_mode', '').strip().lower() != 'random':
        return _original_send_article(index)
    random_url = request.form.get('random_image_url', '').strip()
    if not random_url:
        raise RuntimeError('Seleziona prima un’immagine casuale.')
    image_path = download_random_image(random_url)
    original_files_get = request.files.get
    original_form_get = request.form.get
    class _UploadedRandomImage:
        filename = image_path.name
        def save(self, destination):
            Path(destination).write_bytes(image_path.read_bytes())
    def patched_files_get(key, default=None):
        if key == 'image_upload':
            return _UploadedRandomImage()
        return original_files_get(key, default)
    def patched_form_get(key, default=None, type=None):
        if key == 'image_mode':
            return 'upload'
        return original_form_get(key, default, type=type)
    request.files.get = patched_files_get
    request.form.get = patched_form_get
    try:
        response = _original_send_article(index)
        remember_random_image(random_url)
        return response
    finally:
        request.files.get = original_files_get
        request.form.get = original_form_get

app.view_functions['send_article_route'] = runtime_send_article

@app.context_processor
def inject_runtime_version():
    return {'app_version': RUNTIME_APP_VERSION}
