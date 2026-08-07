import fixed_app as fixed

base = fixed.base
app = fixed.app

CONFIRMATION_LOG_FILE = base.DATA_DIR / 'confirmation_log.txt'
DEFAULT_IMAGE_URL = 'https://www.montagneepaesi.com/wp-content/uploads/2026/07/opengraph_qrcode-scaled-1.png'
DEFAULT_IMAGE_FILENAME = 'immagine-default-montagne-e-paesi.png'
MIN_EDITORIAL_IMAGE_BYTES = 50 * 1024
UPLOAD_IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp'}

CATEGORY_NAMES = {
    'nazionali-ed-internazionali': 'Nazionali ed Internazionali',
    'notizie-nazionali': 'Notizie Nazionali',
    'notizie-internazionali': 'Notizie Internazionali',
    'provincia-di-bergamo': 'Provincia di Bergamo',
    'bergamo-ed-hinterland': 'Bergamo ed Hinterland',
    'valseriana': 'Val Seriana',
    'valbrembana': 'Val Brembana',
    'valleimagna': 'Valle Imagna',
    'vallecavallina': 'Valle Cavallina',
    'provincia-di-brescia': 'Provincia di Brescia',
    'brescia-ed-hinterland': 'Brescia ed Hinterland',
    'vallecamonica': 'Valle Camonica',
    'sebino': 'Sebino',
    'franciacorta-notizie': 'Franciacorta Notizie',
    'valle-trompia': 'Valle Trompia',
    'provincia-di-sondrio': 'Provincia di Sondrio',
    'valtellina': 'Valtellina',
    'alta-valtellina': 'Alta Valtellina',
    'media-valtellina': 'Media Valtellina',
    'sondrio-ed-hinterland': 'Sondrio ed Hinterland',
    'salute': 'Salute',
    'tecnologia': 'Tecnologia',
    'wine': 'Wine',
}


def ensure_default_image():
    from urllib.request import Request, urlopen

    base.ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)
    image_path = base.ATTACHMENTS_DIR / DEFAULT_IMAGE_FILENAME
    if image_path.exists() and image_path.stat().st_size > 1024:
        return image_path

    request = Request(
        DEFAULT_IMAGE_URL,
        headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36',
            'Accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',
            'Referer': 'https://www.montagneepaesi.com/',
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            data = response.read()
            content_type = (response.headers.get('Content-Type') or '').lower()
    except Exception as exc:
        raise RuntimeError(f'Impossibile scaricare l’immagine predefinita: {type(exc).__name__}: {exc}') from exc

    if not data or len(data) < 1024:
        raise RuntimeError('Il server ha restituito un file immagine vuoto o troppo piccolo.')
    if content_type and not content_type.startswith('image/'):
        raise RuntimeError(f'Il file predefinito non risulta un’immagine: {content_type}')

    image_path.write_bytes(data)
    base.log(f'Immagine predefinita scaricata: {image_path.name} ({len(data)} byte)')
    return image_path


def editorial_images(images):
    valid = []
    for image in images or []:
        try:
            if image.name == DEFAULT_IMAGE_FILENAME:
                continue
            if image.stat().st_size >= MIN_EDITORIAL_IMAGE_BYTES:
                valid.append(image)
            else:
                base.log(f'Immagine ignorata perché probabilmente logo/firma: {image.name} ({image.stat().st_size} byte)')
        except Exception as exc:
            base.log_exception('Errore controllo immagine allegata', exc)
    return valid


def save_uploaded_article_image(upload):
    from pathlib import Path
    from datetime import datetime

    if not upload or not upload.filename:
        raise RuntimeError('Seleziona un file immagine da caricare.')

    safe_name = base.safe_filename(upload.filename)
    suffix = Path(safe_name).suffix.lower()
    if suffix not in UPLOAD_IMAGE_EXTENSIONS:
        raise RuntimeError('Formato immagine non supportato. Usa JPG, PNG o WEBP.')

    payload = upload.read()
    if not payload:
        raise RuntimeError('Il file immagine caricato è vuoto.')

    base.ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"immagine-articolo-{datetime.now().strftime('%Y%m%d-%H%M%S')}{suffix}"
    path = base.ATTACHMENTS_DIR / filename
    path.write_bytes(payload)
    base.log(f'Immagine articolo caricata manualmente: {filename} ({len(payload)} byte)')
    return path


def write_confirmation_log(email, title, status='OK', error=''):
    try:
        from datetime import datetime
        CONFIRMATION_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        line = f'{stamp} | {status} | {email or ""} | {title or ""}'
        if error:
            line += f' | {error}'
        with CONFIRMATION_LOG_FILE.open('a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception as exc:
        base.log_exception('Errore scrittura storico conferme', exc)


def read_confirmation_log(limit=300):
    if not CONFIRMATION_LOG_FILE.exists():
        return ''
    try:
        return '\n'.join(CONFIRMATION_LOG_FILE.read_text(encoding='utf-8', errors='replace').splitlines()[-limit:])
    except Exception as exc:
        base.log_exception('Errore lettura storico conferme', exc)
        return ''


def build_postie_subject(title, selected_categories):
    category_names = []
    accepted_slugs = []
    seen = set()
    for category_slug in selected_categories:
        category_slug = str(category_slug or '').strip()
        category_name = CATEGORY_NAMES.get(category_slug)
        if category_name and category_slug not in seen:
            category_names.append(category_name)
            accepted_slugs.append(category_slug)
            seen.add(category_slug)
    prefix = ' '.join(f'[{category_name}]' for category_name in category_names)
    return f'{prefix} {title}'.strip(), accepted_slugs


def patched_index():
    from flask import render_template
    cfg = base.load_config()
    files, images, docs, size = base.attachment_stats()
    logs = ''
    if base.LOG_FILE.exists():
        logs = '\n'.join(base.LOG_FILE.read_text(encoding='utf-8', errors='replace').splitlines()[-120:])
    return render_template(
        'index.html',
        title=base.APP_TITLE,
        cfg=cfg,
        mails=fixed.previews(),
        files=files,
        images=images,
        docs=docs,
        total_size=base.bytes_to_readable(size),
        logs=logs,
        confirmation_logs=read_confirmation_log(),
    )


def patched_generate_route(index):
    from flask import flash, redirect, render_template, url_for
    from bs4 import BeautifulSoup

    cfg = base.load_config()
    item = None
    try:
        item = fixed.find_item(index, cfg)
        msg = base.fetch_full_message_for_item(item, cfg)
        original_subject, sender, date = base.get_email_preview(msg)
        email_body = base.extract_body_from_email(msg)
        attachments, images, documents_text = base.save_attachments_and_extract_text(msg)
        source_text = f'''OGGETTO EMAIL:\n{original_subject}\n\nMITTENTE:\n{sender}\n\nDATA EMAIL:\n{date}\n\nTESTO DELLA MAIL:\n{email_body}\n\n{documents_text}'''.strip()
        if len(source_text) < 100:
            flash('Testo insufficiente per generare un articolo.', 'warning')
            return redirect(url_for('view_mail', index=item.server_id))

        article_title, html_article = base.generate_article(source_text, cfg)
        sender_email = base.extract_sender_email(msg)

        editorial = editorial_images(images)
        has_editorial_images = bool(editorial)
        image_names = [p.name for p in editorial]
        selected_image = fixed.largest_image_name(editorial) if editorial else ''

        default_image_available = False
        try:
            default_image = ensure_default_image()
            default_image_available = True
            if default_image.name not in image_names:
                image_names.append(default_image.name)
        except Exception as default_exc:
            base.log_exception('Errore preparazione immagine predefinita', default_exc)

        article_text = BeautifulSoup(html_article, 'html.parser').get_text(' ', strip=True)
        image_prompt = f'Generami un’immagine per questo articolo: {article_title}. {article_text}'

        return render_template(
            'preview.html',
            title=base.APP_TITLE,
            index=item.server_id,
            article_title=article_title,
            html_article=html_article,
            image_names=image_names,
            selected_image=selected_image,
            sender_email=sender_email,
            has_editorial_images=has_editorial_images,
            default_image_filename=DEFAULT_IMAGE_FILENAME,
            default_image_available=default_image_available,
            image_prompt=image_prompt,
        )
    except Exception as exc:
        base.log_exception('Errore generazione articolo', exc)
        flash(f'Errore generazione articolo: {type(exc).__name__}: {exc}', 'danger')
        if item is not None:
            try:
                return fixed.render_open_mail(item, cfg)
            except Exception as open_exc:
                base.log_exception('Errore riapertura mail dopo errore AI', open_exc)
        return redirect(url_for('view_mail', index=index))


def patched_send_preview_route(index):
    from flask import flash, redirect, request, url_for
    cfg = base.load_config()
    try:
        title = request.form.get('article_title', '').strip()
        html_article = request.form.get('html_article', '').strip()
        sender_email = request.form.get('sender_email', '').strip()
        selected_categories = request.form.getlist('categories')
        send_confirmation = bool(request.form.get('send_confirmation'))
        delete_after_send = bool(request.form.get('delete_after_send'))
        image_mode = request.form.get('image_mode', 'existing').strip()
        image_filename = ''

        if not title or not html_article:
            flash('Titolo e articolo non possono essere vuoti.', 'warning')
            return redirect(url_for('index'))

        if image_mode == 'none':
            image_filename = ''
        elif image_mode == 'default':
            image_filename = ensure_default_image().name
        elif image_mode == 'upload':
            image_filename = save_uploaded_article_image(request.files.get('image_upload')).name
        else:
            image_filename = request.form.get('image_filename', '').strip()

        postie_subject, accepted_categories = build_postie_subject(title, selected_categories)
        base.send_result_email(postie_subject, html_article, image_filename, cfg)

        if send_confirmation and sender_email:
            try:
                base.send_confirmation_email_to_sender(sender_email, title, cfg)
                write_confirmation_log(sender_email, title, 'OK')
            except Exception as conf_exc:
                write_confirmation_log(sender_email, title, 'ERRORE', f'{type(conf_exc).__name__}: {conf_exc}')
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

        if delete_after_send:
            try:
                item_to_delete = fixed.find_item(index, cfg, refresh=False)
                deleted_count, failed = base.delete_messages([item_to_delete], cfg)
                if failed or deleted_count == 0:
                    msg += ' Attenzione: non sono riuscito a cancellare automaticamente la mail originale.'
                    flash(msg, 'warning')
                else:
                    base.refresh_mail_cache(cfg)
                    msg += ' Mail originale cancellata automaticamente.'
                    flash(msg, 'success')
            except Exception as del_exc:
                base.log_exception('Errore cancellazione automatica mail originale', del_exc)
                msg += ' Attenzione: non sono riuscito a trovare la mail originale da cancellare.'
                flash(msg, 'warning')
        else:
            flash(msg, 'success')
        base.log(f'Email articolo inviata: {title}; categorie: {accepted_categories}; modalità immagine: {image_mode}; immagine: {image_filename}; cancellazione automatica mail: {delete_after_send}')
    except Exception as exc:
        base.log_exception('Errore invio email', exc)
        flash(f'Errore invio email: {type(exc).__name__}: {exc}', 'danger')
    return redirect(url_for('index'))


def clear_confirmation_logs_route():
    from flask import flash, redirect, url_for
    try:
        if CONFIRMATION_LOG_FILE.exists():
            CONFIRMATION_LOG_FILE.unlink()
        flash('Storico conferme svuotato.', 'success')
    except Exception as exc:
        base.log_exception('Errore pulizia storico conferme', exc)
        flash(f'Errore pulizia storico conferme: {type(exc).__name__}: {exc}', 'danger')
    return redirect(url_for('index'))


app.view_functions['index'] = patched_index
app.view_functions['generate_route'] = patched_generate_route
app.view_functions['send_preview_route'] = patched_send_preview_route
app.add_url_rule('/clear-confirmation-logs', endpoint='clear_confirmation_logs_route', view_func=clear_confirmation_logs_route, methods=['POST'])
