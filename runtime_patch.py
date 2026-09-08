from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
import json
import random
import time

import confirmation_patch as cp

# Correzioni/integrazioni categorie Postie/WordPress.
cp.CATEGORY_NAMES['valseriana'] = 'Valle Seriana'
cp.CATEGORY_NAMES['val-gandino'] = 'Val Gandino'

# Libreria unica di immagini casuali Montagne & Paesi.
RANDOM_IMAGE_LIBRARY_URL = 'https://www.montagneepaesi.com/random_images/'
RANDOM_IMAGE_LIST_URL = urljoin(RANDOM_IMAGE_LIBRARY_URL, 'images.php')
RANDOM_IMAGE_EXTENSIONS = ('.jpg', '.jpeg', '.png', '.webp')
RANDOM_IMAGE_HISTORY_FILE = cp.base.DATA_DIR / 'random_image_history.json'
RANDOM_IMAGE_HISTORY_LIMIT = 30


def _request(url, accept=None):
    return Request(
        url,
        headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36',
            'Accept': accept or 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8',
            'Referer': 'https://www.montagneepaesi.com/',
        },
    )


def _valid_random_image_url(url):
    try:
        base = urlparse(RANDOM_IMAGE_LIBRARY_URL)
        candidate = urlparse(url)
        return (
            candidate.scheme in ('http', 'https')
            and candidate.netloc == base.netloc
            and candidate.path.startswith(base.path)
            and candidate.path.lower().endswith(RANDOM_IMAGE_EXTENSIONS)
        )
    except Exception:
        return False


def list_random_images():
    """Legge images.php e restituisce gli URL completi delle immagini supportate."""
    try:
        with urlopen(_request(RANDOM_IMAGE_LIST_URL, 'application/json,text/plain;q=0.9,*/*;q=0.8'), timeout=20) as response:
            payload = response.read().decode('utf-8', errors='replace')
            content_type = (response.headers.get('Content-Type') or '').lower()
    except Exception as exc:
        raise RuntimeError(f'Impossibile leggere la libreria immagini: {type(exc).__name__}: {exc}') from exc

    try:
        data = json.loads(payload)
    except Exception as exc:
        raise RuntimeError('La libreria immagini non restituisce un JSON valido.') from exc

    if not isinstance(data, list):
        raise RuntimeError('La libreria immagini deve restituire un elenco JSON di file.')

    images = []
    seen = set()
    for item in data:
        if not isinstance(item, str):
            continue
        filename = item.strip()
        if not filename:
            continue
        url = urljoin(RANDOM_IMAGE_LIBRARY_URL, filename)
        if _valid_random_image_url(url) and url not in seen:
            images.append(url)
            seen.add(url)

    if not images:
        detail = f' Content-Type: {content_type}.' if content_type else ''
        raise RuntimeError(f'Nessuna immagine valida trovata in random_images.{detail}')
    return images


def _read_random_history():
    try:
        if not RANDOM_IMAGE_HISTORY_FILE.exists():
            return []
        data = json.loads(RANDOM_IMAGE_HISTORY_FILE.read_text(encoding='utf-8'))
        return [str(x) for x in data if _valid_random_image_url(str(x))]
    except Exception as exc:
        cp.base.log_exception('Errore lettura storico immagini casuali', exc)
        return []


def _record_random_image(url):
    if not _valid_random_image_url(url):
        return
    try:
        history = [x for x in _read_random_history() if x != url]
        history.append(url)
        history = history[-RANDOM_IMAGE_HISTORY_LIMIT:]
        RANDOM_IMAGE_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        RANDOM_IMAGE_HISTORY_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception as exc:
        cp.base.log_exception('Errore salvataggio storico immagini casuali', exc)


def choose_random_image(current_url=''):
    images = list_random_images()
    history = set(_read_random_history())
    available = [u for u in images if u not in history and u != current_url]

    # Se tutte le immagini sono già nello storico, evita almeno quella corrente.
    if not available:
        available = [u for u in images if u != current_url]
    if not available:
        available = images
    return random.choice(available)


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

        return render_template(
            'preview.html',
            title=cp.base.APP_TITLE,
            index=item.server_id,
            article_title=article_title,
            html_article=html_article,
            image_names=image_names,
            selected_image=selected_image,
            sender_email=sender_email,
            has_editorial_images=has_editorial_images,
            default_image_filename=cp.DEFAULT_IMAGE_FILENAME,
            default_image_available=default_image_available,
            image_prompt=image_prompt,
            random_image_url=random_image_url,
            random_library_url=RANDOM_IMAGE_LIBRARY_URL,
        )
    except Exception as exc:
        cp.base.log_exception('Errore generazione articolo', exc)
        flash(f'Errore generazione articolo: {type(exc).__name__}: {exc}', 'danger')
        if item is not None:
            try:
                return cp.fixed.render_open_mail(item, cfg)
            except Exception as open_exc:
                cp.base.log_exception('Errore riapertura mail dopo errore AI', open_exc)
        return redirect(url_for('view_mail', index=index))


def random_image_route():
    from flask import jsonify, request
    try:
        current = request.args.get('current', '').strip()
        url = choose_random_image(current)
        return jsonify({'ok': True, 'url': url})
    except Exception as exc:
        cp.base.log_exception('Errore scelta immagine casuale', exc)
        return jsonify({'ok': False, 'error': str(exc)}), 500


def runtime_send_preview_route(index):
    from flask import flash, redirect, request, url_for
    cfg = cp.base.load_config()
    try:
        title = request.form.get('article_title', '').strip()
        html_article = request.form.get('html_article', '').strip()
        sender_email = request.form.get('sender_email', '').strip()
        selected_categories = request.form.getlist('categories')
        send_confirmation = bool(request.form.get('send_confirmation'))
        delete_after_send = bool(request.form.get('delete_after_send'))
        image_mode = request.form.get('image_mode', 'existing').strip()
        image_filename = ''
        random_image_url = ''

        if not title or not html_article:
            flash('Titolo e articolo non possono essere vuoti.', 'warning')
            return redirect(url_for('index'))

        if image_mode == 'none':
            image_filename = ''
        elif image_mode == 'default':
            image_filename = cp.ensure_default_image().name
        elif image_mode == 'upload':
            image_filename = cp.save_uploaded_article_image(request.files.get('image_upload')).name
        elif image_mode == 'random':
            random_image_url = request.form.get('random_image_url', '').strip()
            if not _valid_random_image_url(random_image_url):
                random_image_url = choose_random_image()
            image_filename = download_random_image(random_image_url).name
        else:
            image_filename = request.form.get('image_filename', '').strip()

        postie_subject, accepted_categories = cp.build_postie_subject(title, selected_categories)
        cp.base.send_result_email(postie_subject, html_article, image_filename, cfg)

        # Segna come usata solo dopo un invio riuscito.
        if image_mode == 'random' and random_image_url:
            _record_random_image(random_image_url)

        if send_confirmation and sender_email:
            try:
                cp.base.send_confirmation_email_to_sender(sender_email, title, cfg)
                cp.write_confirmation_log(sender_email, title, 'OK')
            except Exception as conf_exc:
                cp.write_confirmation_log(sender_email, title, 'ERRORE', f'{type(conf_exc).__name__}: {conf_exc}')
                raise

        msg = 'Email articolo inviata correttamente.'
        if accepted_categories:
            msg += ' Categorie: ' + ', '.join(accepted_categories) + '.'
        else:
            msg += ' Nessuna categoria specifica selezionata: verrà usata quella predefinita di Postie.'
        if image_mode == 'none':
            msg += ' Nessuna immagine allegata.'
        elif image_mode == 'default':
            msg += ' Utilizzata l’immagine predefinita.'
        elif image_mode == 'upload':
            msg += ' Utilizzata l’immagine caricata manualmente.'
        elif image_mode == 'random':
            msg += ' Utilizzata un’immagine casuale dalla libreria Montagne & Paesi.'

        if delete_after_send:
            try:
                item_to_delete = cp.fixed.find_item(index, cfg, refresh=False)
                deleted_count, failed = cp.base.delete_messages([item_to_delete], cfg)
                if failed or deleted_count == 0:
                    msg += ' Attenzione: non sono riuscito a cancellare automaticamente la mail originale.'
                    flash(msg, 'warning')
                else:
                    cp.base.refresh_mail_cache(cfg)
                    msg += ' Mail originale cancellata automaticamente.'
                    flash(msg, 'success')
            except Exception as del_exc:
                cp.base.log_exception('Errore cancellazione automatica mail originale', del_exc)
                msg += ' Attenzione: non sono riuscito a trovare la mail originale da cancellare.'
                flash(msg, 'warning')
        else:
            flash(msg, 'success')
        cp.base.log(f'Email articolo inviata: {title}; categorie: {accepted_categories}; modalità immagine: {image_mode}; immagine: {image_filename}; cancellazione automatica mail: {delete_after_send}')
    except Exception as exc:
        cp.base.log_exception('Errore invio email', exc)
        flash(f'Errore invio email: {type(exc).__name__}: {exc}', 'danger')
    return redirect(url_for('index'))


# Versione applicazione. Viene esposta direttamente al contesto Flask.
RUNTIME_APP_VERSION = '2.1.1'
cp.APP_VERSION = RUNTIME_APP_VERSION


@cp.app.context_processor
def inject_runtime_app_version():
    return {'app_version': RUNTIME_APP_VERSION}


cp.CHANGELOG.insert(0, {
    'version': '2.1.1',
    'date': '8 settembre 2026',
    'changes': [
        'La libreria immagini casuali ora legge l’elenco da /random_images/images.php in formato JSON.',
        'La cartella /random_images/ può restare protetta dal directory listing e restituire Forbidden.',
        'Migliorata la gestione degli errori quando l’endpoint JSON non è disponibile o non restituisce immagini valide.',
    ],
})
cp.CHANGELOG.insert(1, {
    'version': '2.1.0',
    'date': '8 settembre 2026',
    'changes': [
        'Aggiunta libreria unica di immagini casuali da https://www.montagneepaesi.com/random_images/.',
        'Anteprima dell’immagine casuale e pulsante per cambiarla prima dell’invio.',
        'L’immagine scelta viene scaricata solo al momento dell’invio a WordPress/Postie.',
        'Anti-ripetizione sulle ultime 30 immagini casuali effettivamente pubblicate.',
    ],
})
cp.CHANGELOG.insert(2, {
    'version': '2.0.4',
    'date': '7 settembre 2026',
    'changes': [
        'Corretta la visualizzazione della versione: il numero release viene ora passato direttamente dal runtime al template Flask.',
    ],
})
cp.CHANGELOG.insert(3, {
    'version': '2.0.3',
    'date': '7 settembre 2026',
    'changes': [
        'Aggiunta la categoria WordPress Val Gandino (slug: val-gandino) tra le categorie Bergamo.',
    ],
})
cp.CHANGELOG.insert(4, {
    'version': '2.0.2',
    'date': '7 settembre 2026',
    'changes': [
        'Corretta la categoria WordPress Valle Seriana: Postie ora riceve [Valle Seriana] invece di [Val Seriana].',
        'Risolto il problema per cui [Val Seriana] rimaneva nel titolo pubblicato.',
    ],
})

# Sostituisce le route con le versioni v2.1.x.
cp.app.view_functions['generate_route'] = runtime_generate_route
cp.app.view_functions['send_preview_route'] = runtime_send_preview_route
if 'random_image_route' not in cp.app.view_functions:
    cp.app.add_url_rule('/random-image', endpoint='random_image_route', view_func=random_image_route, methods=['GET'])

app = cp.app
