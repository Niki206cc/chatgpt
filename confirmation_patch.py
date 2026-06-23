import fixed_app as fixed

base = fixed.base
app = fixed.app

CONFIRMATION_LOG_FILE = base.DATA_DIR / 'confirmation_log.txt'


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
        image_names = [p.name for p in images]
        selected_image = fixed.largest_image_name(images)
        return render_template('preview.html', title=base.APP_TITLE, index=item.server_id, article_title=article_title, html_article=html_article, image_names=image_names, selected_image=selected_image, sender_email=sender_email)
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
        image_filename = request.form.get('image_filename', '').strip()
        sender_email = request.form.get('sender_email', '').strip()
        send_confirmation = bool(request.form.get('send_confirmation'))
        delete_after_send = bool(request.form.get('delete_after_send'))
        if not title or not html_article:
            flash('Titolo e articolo non possono essere vuoti.', 'warning')
            return redirect(url_for('index'))
        base.send_result_email(title, html_article, image_filename, cfg)
        if send_confirmation and sender_email:
            try:
                base.send_confirmation_email_to_sender(sender_email, title, cfg)
                write_confirmation_log(sender_email, title, 'OK')
            except Exception as conf_exc:
                write_confirmation_log(sender_email, title, 'ERRORE', f'{type(conf_exc).__name__}: {conf_exc}')
                raise
        msg = 'Email articolo inviata correttamente.'
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
        base.log(f'Email articolo inviata: {title}; cancellazione automatica mail: {delete_after_send}')
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
