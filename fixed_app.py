import app as base
from flask import request, redirect, url_for, flash, render_template

app = base.app


def key(v):
    return str(v or '').strip()


def find_item(v, cfg=None, refresh=True):
    k = key(v)
    for item in base.MAIL_CACHE:
        if str(item.server_id) == k:
            return item
    try:
        i = int(k)
        if 0 <= i < len(base.MAIL_CACHE):
            return base.MAIL_CACHE[i]
    except Exception:
        pass
    if refresh:
        base.refresh_mail_cache(cfg or base.load_config())
        for item in base.MAIL_CACHE:
            if str(item.server_id) == k:
                return item
    raise RuntimeError('Mail non più presente nella lista aggiornata. Premi Carica mail e riprova.')


def previews():
    out = []
    for item in base.MAIL_CACHE:
        subject, sender, date = base.get_email_preview(item.header_msg)
        out.append({
            'i': item.server_id,
            'subject': subject,
            'sender': sender,
            'date': date,
            'size': base.bytes_to_readable(item.size_bytes),
            'size_bytes': int(item.size_bytes or 0),
            'unread': item.is_unread,
        })
    return out


def largest_image_name(images):
    valid = []
    for image in images or []:
        try:
            valid.append((image.stat().st_size, image.name))
        except Exception:
            continue
    if not valid:
        return ''
    valid.sort(reverse=True)
    return valid[0][1]


def fixed_index():
    cfg = base.load_config()
    files, images, docs, size = base.attachment_stats()
    logs = ''
    if base.LOG_FILE.exists():
        logs = '\n'.join(base.LOG_FILE.read_text(encoding='utf-8', errors='replace').splitlines()[-120:])
    return render_template('index.html', title=base.APP_TITLE, cfg=cfg, mails=previews(), files=files, images=images, docs=docs, total_size=base.bytes_to_readable(size), logs=logs)


def fixed_load_mails_route():
    try:
        cfg = base.load_config()
        if not cfg['in_server'] or not cfg['in_user'] or not cfg['in_password']:
            flash('Inserisci server, username e password della casella.', 'warning')
            return redirect(url_for('index'))
        base.refresh_mail_cache(cfg)
        flash(f'Caricate {len(base.MAIL_CACHE)} mail.', 'success')
        base.log(f'Caricate {len(base.MAIL_CACHE)} mail')
    except Exception as e:
        base.log_exception('Errore caricamento mail', e)
        flash(f'Errore caricamento mail: {type(e).__name__}: {e}', 'danger')
    return redirect(url_for('index'))


def fixed_view_mail(index):
    cfg = base.load_config()
    try:
        item = find_item(index, cfg)
        msg = base.fetch_full_message_for_item(item, cfg)
        body = base.extract_body_from_email(msg)
        attachments, images, documents_text = base.save_attachments_and_extract_text(msg)
        subject, sender, date = base.get_email_preview(msg)
        flash(f'Allegati salvati nella cartella persistente: {len(attachments)}', 'success')
        return render_template('mail.html', title=base.APP_TITLE, index=item.server_id, subject=subject, sender=sender, date=date, body=body, attachments=attachments, images=images, documents_text=documents_text)
    except Exception as e:
        base.log_exception('Errore apertura mail', e)
        flash(f'Errore apertura mail: {type(e).__name__}: {e}', 'danger')
        return redirect(url_for('index'))


def fixed_generate_route(index):
    cfg = base.load_config()
    try:
        item = find_item(index, cfg)
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
        selected_image = largest_image_name(images)
        return render_template('preview.html', title=base.APP_TITLE, index=item.server_id, article_title=article_title, html_article=html_article, image_names=image_names, selected_image=selected_image, sender_email=sender_email)
    except Exception as e:
        base.log_exception('Errore generazione articolo', e)
        flash(f'Errore generazione articolo: {type(e).__name__}: {e}', 'danger')
        return redirect(url_for('index'))


def fixed_send_preview_route(index):
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
            base.send_confirmation_email_to_sender(sender_email, title, cfg)
        msg = 'Email articolo inviata correttamente.'
        if delete_after_send:
            try:
                item_to_delete = find_item(index, cfg, refresh=False)
                deleted_count, failed = base.delete_messages([item_to_delete], cfg)
                if failed or deleted_count == 0:
                    msg += ' Attenzione: non sono riuscito a cancellare automaticamente la mail originale.'
                    flash(msg, 'warning')
                else:
                    base.refresh_mail_cache(cfg)
                    msg += ' Mail originale cancellata automaticamente.'
                    flash(msg, 'success')
            except Exception as de:
                base.log_exception('Errore cancellazione automatica mail originale', de)
                msg += ' Attenzione: non sono riuscito a trovare la mail originale da cancellare.'
                flash(msg, 'warning')
        else:
            flash(msg, 'success')
        base.log(f'Email articolo inviata: {title}; cancellazione automatica mail: {delete_after_send}')
    except Exception as e:
        base.log_exception('Errore invio email', e)
        flash(f'Errore invio email: {type(e).__name__}: {e}', 'danger')
    return redirect(url_for('index'))


def fixed_delete_mails_route():
    cfg = base.load_config()
    try:
        selected = [key(x) for x in request.form.getlist('mail_indexes') if key(x)]
        items = []
        missing = []
        for k in selected:
            try:
                items.append(find_item(k, cfg, refresh=False))
            except Exception:
                missing.append(k)
        if not items:
            flash('Nessuna mail selezionata ancora presente. Premi Carica mail e riprova.', 'warning')
            return redirect(url_for('index'))
        deleted_count, failed = base.delete_messages(items, cfg)
        try:
            base.refresh_mail_cache(cfg)
        except Exception as re:
            base.log_exception('Errore aggiornamento elenco dopo cancellazione', re)
            failed_set = {str(x) for x in failed}
            deleted_set = {str(item.server_id) for item in items if str(item.server_id) not in failed_set}
            base.MAIL_CACHE = [item for item in base.MAIL_CACHE if str(item.server_id) not in deleted_set]
        if failed or missing:
            flash(f'Cancellate {deleted_count} mail. {len(failed) + len(missing)} non sono state confermate o non erano più presenti: ricarica la casella e riprova.', 'warning')
        else:
            flash(f'Cancellate {deleted_count} mail dalla casella.', 'success')
        base.log(f'Cancellazione mail richiesta: {len(selected)}; cancellate: {deleted_count}; fallite/non trovate: {len(failed) + len(missing)}')
    except Exception as e:
        base.log_exception('Errore cancellazione mail', e)
        flash(f'Errore cancellazione mail: {type(e).__name__}: {e}', 'danger')
    return redirect(url_for('index'))


def fixed_delete_one_mail_route(index):
    cfg = base.load_config()
    try:
        item = find_item(index, cfg, refresh=False)
        deleted_count, failed = base.delete_messages([item], cfg)
        try:
            base.refresh_mail_cache(cfg)
        except Exception as re:
            base.log_exception('Errore aggiornamento elenco dopo cancellazione singola', re)
            if not failed and deleted_count:
                base.MAIL_CACHE = [m for m in base.MAIL_CACHE if str(m.server_id) != str(item.server_id)]
        if failed or deleted_count == 0:
            flash('La mail non è stata cancellata dal server. Ricarica la casella e riprova.', 'warning')
        else:
            flash('Mail cancellata dalla casella.', 'success')
    except Exception as e:
        base.log_exception('Errore cancellazione diretta mail', e)
        flash(f'Errore cancellazione mail: {type(e).__name__}: {e}', 'danger')
    return redirect(url_for('index'))


app.view_functions['index'] = fixed_index
app.view_functions['load_mails_route'] = fixed_load_mails_route
app.view_functions['view_mail'] = fixed_view_mail
app.view_functions['generate_route'] = fixed_generate_route
app.view_functions['send_preview_route'] = fixed_send_preview_route
app.view_functions['delete_mails_route'] = fixed_delete_mails_route
app.add_url_rule('/delete-mail/<path:index>', endpoint='delete_one_mail_route', view_func=fixed_delete_one_mail_route, methods=['POST'])
