
import os
import ssl
import imaplib
import poplib
import smtplib
import configparser
import traceback
import logging
import shutil
from pathlib import Path
from datetime import datetime
from email import policy
from email.parser import BytesParser
from email.header import decode_header
from email.message import EmailMessage
from email.utils import parseaddr
from flask import Flask, render_template, request, redirect, url_for, flash, send_from_directory, abort

from bs4 import BeautifulSoup

try:
    from google import genai
except Exception:
    genai = None

try:
    from openai import OpenAI
except Exception:
    OpenAI = None

try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None

try:
    from pypdf import PdfReader
except Exception:
    PdfReader = None

try:
    import docx
except Exception:
    docx = None


APP_TITLE = "Automazione articoli Montagne & Paesi"
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
ATTACHMENTS_DIR = Path(os.environ.get("ATTACHMENTS_DIR", "/attachments"))
CONFIG_FILE = DATA_DIR / "config_automazione.txt"
LOG_FILE = DATA_DIR / "automazione_articoli.log"
DESTINATARIO_DEFAULT = "maildalsito@montagneepaesi.com"

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
DOCUMENT_EXTENSIONS = {".pdf", ".docx", ".txt"}

DATA_DIR.mkdir(parents=True, exist_ok=True)
ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    filename=str(LOG_FILE),
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    encoding="utf-8"
)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "9fK2xPqL8sVtW3nR6zY1bC4mD7uH5eJ0QxT2aL9p")

MAIL_CACHE = []


class MailItem:
    def __init__(self, protocol, server_id, header_msg, is_unread=False, size_bytes=0):
        self.protocol = protocol
        self.server_id = server_id
        self.header_msg = header_msg
        self.full_msg = None
        self.is_unread = is_unread
        self.size_bytes = size_bytes


def log(text):
    logging.info(text)


def log_exception(context, exc):
    logging.error("%s: %s: %s", context, type(exc).__name__, exc)
    logging.error(traceback.format_exc())


def decode_mime_words(value):
    if not value:
        return ""
    decoded_parts = decode_header(value)
    result = ""
    for part, encoding in decoded_parts:
        if isinstance(part, bytes):
            try:
                result += part.decode(encoding or "utf-8", errors="replace")
            except Exception:
                result += part.decode("utf-8", errors="replace")
        else:
            result += part
    return result.strip()


def safe_filename(name, max_stem_length=70):
    if not name:
        name = "allegato"
    name = decode_mime_words(name).replace("\r", " ").replace("\n", " ").strip()
    for ch in '<>:"/\\|?*':
        name = name.replace(ch, "_")
    name = "".join(ch for ch in name if ord(ch) >= 32)
    name = " ".join(name.split()).strip(" .") or "allegato"
    path = Path(name)
    suffix = path.suffix.lower()
    stem = path.stem or "allegato"
    if len(stem) > max_stem_length:
        stem = stem[:max_stem_length].rstrip(" ._-")
    return f"{stem}{suffix}" if suffix else stem


def unique_attachment_path(filename, counter):
    safe_name = safe_filename(filename)
    candidate = ATTACHMENTS_DIR / safe_name
    if not candidate.exists():
        return candidate
    stem, suffix = candidate.stem, candidate.suffix
    for i in range(2, 1000):
        new_candidate = ATTACHMENTS_DIR / f"{stem}_{i}{suffix}"
        if not new_candidate.exists():
            return new_candidate
    return ATTACHMENTS_DIR / f"allegato_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{counter}{suffix}"


def html_to_text(html):
    soup = BeautifulSoup(html, "html.parser")
    return soup.get_text("\n", strip=True)


def extract_body_from_email(msg):
    plain_parts, html_parts = [], []
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = part.get_content_disposition()
            if disposition == "attachment":
                continue
            try:
                payload = part.get_payload(decode=True)
                charset = part.get_content_charset() or "utf-8"
                if not payload:
                    continue
                text = payload.decode(charset, errors="replace")
                if content_type == "text/plain":
                    plain_parts.append(text)
                elif content_type == "text/html":
                    html_parts.append(html_to_text(text))
            except Exception:
                continue
    else:
        try:
            payload = msg.get_payload(decode=True)
            charset = msg.get_content_charset() or "utf-8"
            if payload:
                text = payload.decode(charset, errors="replace")
                if msg.get_content_type() == "text/html":
                    html_parts.append(html_to_text(text))
                else:
                    plain_parts.append(text)
        except Exception:
            pass
    return "\n\n".join(plain_parts or html_parts).strip()


def extract_pdf_text(path):
    if PdfReader is None:
        return ""
    try:
        reader = PdfReader(str(path))
        return "\n\n".join((page.extract_text() or "") for page in reader.pages).strip()
    except Exception:
        return ""


def extract_docx_text(path):
    if docx is None:
        return ""
    try:
        document = docx.Document(str(path))
        return "\n".join(p.text for p in document.paragraphs if p.text.strip()).strip()
    except Exception:
        return ""


def extract_txt_text(path):
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace").strip()
    except Exception:
        return ""


def save_attachments_and_extract_text(msg):
    attachments, images, document_texts = [], [], []
    counter = 0
    ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)
    for part in msg.walk():
        if part.get_content_disposition() != "attachment":
            continue
        original_filename = part.get_filename() or f"allegato_{counter + 1}"
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        counter += 1
        file_path = unique_attachment_path(original_filename, counter)
        file_path.write_bytes(payload)
        suffix = file_path.suffix.lower()
        attachments.append(file_path)
        if suffix in IMAGE_EXTENSIONS:
            images.append(file_path)
        if suffix in DOCUMENT_EXTENSIONS:
            if suffix == ".pdf":
                text = extract_pdf_text(file_path)
            elif suffix == ".docx":
                text = extract_docx_text(file_path)
            elif suffix == ".txt":
                text = extract_txt_text(file_path)
            else:
                text = ""
            if text:
                document_texts.append(f"\n\n--- TESTO ESTRATTO DA ALLEGATO: {file_path.name} ---\n{text}")
    return attachments, images, "\n".join(document_texts).strip()


def get_email_preview(msg):
    return (
        decode_mime_words(msg.get("Subject", "Senza oggetto")),
        decode_mime_words(msg.get("From", "")),
        decode_mime_words(msg.get("Date", "")),
    )


def extract_sender_email(msg):
    _, address = parseaddr(decode_mime_words(msg.get("From", "")))
    return address.strip()


def bytes_to_readable(size_bytes):
    try:
        size_bytes = int(size_bytes)
    except Exception:
        return ""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


def italian_today_string():
    weekdays = ["lunedì","martedì","mercoledì","giovedì","venerdì","sabato","domenica"]
    months = ["gennaio","febbraio","marzo","aprile","maggio","giugno","luglio","agosto","settembre","ottobre","novembre","dicembre"]
    try:
        now = datetime.now(ZoneInfo("Europe/Rome")) if ZoneInfo else datetime.now()
    except Exception:
        now = datetime.now()
    return f"{weekdays[now.weekday()]} {now.day} {months[now.month - 1]} {now.year}"


def default_config():
    return {
        "protocol": "IMAP",
        "in_server": "ssl0.ovh.net",
        "in_port": "993",
        "in_user": "",
        "in_password": "",
        "only_unread": False,
        "smtp_server": "ssl0.ovh.net",
        "smtp_port": "465",
        "smtp_user": "",
        "smtp_password": "",
        "smtp_tls": False,
        "smtp_verify_ssl": False,
        "recipient": DESTINATARIO_DEFAULT,
        "ai_provider": "Gemini",
        "gemini_key": "",
        "gemini_model": "gemini-2.5-flash",
        "openai_key": "",
        "openai_model": "gpt-4.1-mini",
    }


def load_config():
    cfg = default_config()
    if not CONFIG_FILE.exists():
        return cfg
    parser = configparser.ConfigParser()
    parser.read(CONFIG_FILE, encoding="utf-8")
    cfg.update({
        "protocol": parser.get("MAIL_IN", "protocol", fallback=cfg["protocol"]),
        "in_server": parser.get("MAIL_IN", "server", fallback=cfg["in_server"]),
        "in_port": parser.get("MAIL_IN", "port", fallback=cfg["in_port"]),
        "in_user": parser.get("MAIL_IN", "username", fallback=cfg["in_user"]),
        "in_password": parser.get("MAIL_IN", "password", fallback=cfg["in_password"]),
        "only_unread": parser.getboolean("MAIL_IN", "only_unread", fallback=cfg["only_unread"]),
        "smtp_server": parser.get("SMTP", "server", fallback=cfg["smtp_server"]),
        "smtp_port": parser.get("SMTP", "port", fallback=cfg["smtp_port"]),
        "smtp_user": parser.get("SMTP", "username", fallback=cfg["smtp_user"]),
        "smtp_password": parser.get("SMTP", "password", fallback=cfg["smtp_password"]),
        "smtp_tls": parser.getboolean("SMTP", "use_starttls", fallback=cfg["smtp_tls"]),
        "smtp_verify_ssl": parser.getboolean("SMTP", "verify_ssl", fallback=cfg["smtp_verify_ssl"]),
        "recipient": parser.get("SMTP", "recipient", fallback=cfg["recipient"]),
        "ai_provider": parser.get("AI", "provider", fallback=cfg["ai_provider"]),
        "gemini_key": parser.get("GEMINI", "api_key", fallback=cfg["gemini_key"]),
        "gemini_model": parser.get("GEMINI", "model", fallback=cfg["gemini_model"]),
        "openai_key": parser.get("OPENAI", "api_key", fallback=cfg["openai_key"]),
        "openai_model": parser.get("OPENAI", "model", fallback=cfg["openai_model"]),
    })
    return cfg


def save_config_from_form(form):
    parser = configparser.ConfigParser()
    parser["MAIL_IN"] = {
        "protocol": form.get("protocol", "IMAP"),
        "server": form.get("in_server", ""),
        "port": form.get("in_port", "993"),
        "username": form.get("in_user", ""),
        "password": form.get("in_password", ""),
        "only_unread": "true" if form.get("only_unread") else "false",
    }
    parser["SMTP"] = {
        "server": form.get("smtp_server", ""),
        "port": form.get("smtp_port", "465"),
        "username": form.get("smtp_user", ""),
        "password": form.get("smtp_password", ""),
        "use_starttls": "true" if form.get("smtp_tls") else "false",
        "verify_ssl": "true" if form.get("smtp_verify_ssl") else "false",
        "recipient": form.get("recipient", DESTINATARIO_DEFAULT) or DESTINATARIO_DEFAULT,
    }
    parser["AI"] = {"provider": form.get("ai_provider", "Gemini")}
    parser["GEMINI"] = {
        "api_key": form.get("gemini_key", ""),
        "model": form.get("gemini_model", "gemini-2.5-flash"),
    }
    parser["OPENAI"] = {
        "api_key": form.get("openai_key", ""),
        "model": form.get("openai_model", "gpt-4.1-mini"),
    }
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        parser.write(f)


def fetch_imap_headers(cfg):
    items = []
    mail = imaplib.IMAP4_SSL(cfg["in_server"], int(cfg["in_port"]), timeout=30)
    mail.login(cfg["in_user"], cfg["in_password"])
    mail.select("INBOX")
    try:
        search_query = "UNSEEN" if cfg["only_unread"] else "ALL"
        status, data = mail.uid("search", None, search_query)
        if status != "OK":
            raise RuntimeError("Impossibile cercare le email nella casella IMAP.")
        uids = data[0].split()[-80:]
        for uid in reversed(uids):
            status, msg_data = mail.uid("fetch", uid, "(FLAGS RFC822.SIZE BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
            if status != "OK":
                continue
            flags_raw, header_bytes, size_bytes = b"", b"", 0
            for part in msg_data:
                if isinstance(part, tuple):
                    meta, body = part[0], part[1]
                    if isinstance(meta, bytes):
                        flags_raw += meta
                        meta_text = meta.decode("utf-8", errors="ignore")
                    else:
                        meta_text = str(meta)
                    if "RFC822.SIZE" in meta_text:
                        try:
                            size_bytes = int(meta_text.split("RFC822.SIZE", 1)[1].strip().split()[0].strip(")"))
                        except Exception:
                            size_bytes = 0
                    if body:
                        header_bytes += body
            header_msg = BytesParser(policy=policy.default).parsebytes(header_bytes)
            items.append(MailItem("IMAP", uid.decode("ascii"), header_msg, b"\\Seen" not in flags_raw, size_bytes))
    finally:
        mail.logout()
    return items


def fetch_pop3_headers(cfg):
    items = []
    pop = poplib.POP3_SSL(cfg["in_server"], int(cfg["in_port"]), timeout=30)
    pop.user(cfg["in_user"])
    pop.pass_(cfg["in_password"])
    try:
        count, _ = pop.stat()
        sizes = {}
        try:
            _, listings, _ = pop.list()
            for row in listings:
                parts = row.decode("utf-8", errors="ignore").split()
                if len(parts) >= 2:
                    sizes[int(parts[0])] = int(parts[1])
        except Exception:
            pass
        start = max(1, count - 79)
        for i in range(count, start - 1, -1):
            _, lines, _ = pop.top(i, 0)
            header_msg = BytesParser(policy=policy.default).parsebytes(b"\n".join(lines))
            items.append(MailItem("POP3", str(i), header_msg, False, sizes.get(i, 0)))
    finally:
        pop.quit()
    return items


def fetch_full_message_for_item(item, cfg):
    if item.full_msg is not None:
        return item.full_msg
    if item.protocol == "IMAP":
        mail = imaplib.IMAP4_SSL(cfg["in_server"], int(cfg["in_port"]), timeout=60)
        mail.login(cfg["in_user"], cfg["in_password"])
        mail.select("INBOX")
        try:
            status, msg_data = mail.uid("fetch", item.server_id, "(BODY.PEEK[])")
            if status != "OK":
                raise RuntimeError("Impossibile scaricare la mail completa.")
            raw_email = next((part[1] for part in msg_data if isinstance(part, tuple)), None)
            if raw_email is None:
                raise RuntimeError("Risposta IMAP senza contenuto email.")
            item.full_msg = BytesParser(policy=policy.default).parsebytes(raw_email)
        finally:
            mail.logout()
    else:
        pop = poplib.POP3_SSL(cfg["in_server"], int(cfg["in_port"]), timeout=60)
        pop.user(cfg["in_user"])
        pop.pass_(cfg["in_password"])
        try:
            _, lines, _ = pop.retr(int(item.server_id))
            item.full_msg = BytesParser(policy=policy.default).parsebytes(b"\n".join(lines))
        finally:
            pop.quit()
    return item.full_msg


def delete_messages(items, cfg):
    if not items:
        return
    if cfg["protocol"] == "IMAP":
        mail = imaplib.IMAP4_SSL(cfg["in_server"], int(cfg["in_port"]), timeout=30)
        mail.login(cfg["in_user"], cfg["in_password"])
        mail.select("INBOX")
        try:
            for item in items:
                mail.uid("store", item.server_id, "+FLAGS", "(\\Deleted)")
            mail.expunge()
        finally:
            mail.logout()
    else:
        pop = poplib.POP3_SSL(cfg["in_server"], int(cfg["in_port"]), timeout=30)
        pop.user(cfg["in_user"])
        pop.pass_(cfg["in_password"])
        try:
            for item in sorted(items, key=lambda x: int(x.server_id), reverse=True):
                pop.dele(int(item.server_id))
        finally:
            pop.quit()


def clean_title(title):
    for token in ["<strong>", "</strong>", "<b>", "</b>", "#", "*"]:
        title = title.replace(token, "")
    return title.strip(" \"'“”‘’")


def generate_article(source_text, cfg):
    prompt = f"""
Devi trasformare il testo seguente in un articolo giornalistico per il sito locale Montagne & Paesi.

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
- Usa in modo naturale parole chiave locali quando pertinenti: incidente, oggi, Bergamo, Brescia, Val Seriana, Valle Camonica.
- Non firmare l'articolo.

DATA REALE DI OGGI:
{italian_today_string()}

TESTO DA TRASFORMARE:
{source_text}
""".strip()

    provider = cfg["ai_provider"]
    if provider == "OpenAI":
        if OpenAI is None:
            raise RuntimeError("Libreria OpenAI non installata.")
        if not cfg["openai_key"]:
            raise RuntimeError("Inserisci la OpenAI API Key.")
        client = OpenAI(api_key=cfg["openai_key"])
        response = client.responses.create(model=cfg["openai_model"] or "gpt-4.1-mini", input=prompt)
        output = (response.output_text or "").strip()
    else:
        if genai is None:
            raise RuntimeError("Libreria Google GenAI non installata.")
        if not cfg["gemini_key"]:
            raise RuntimeError("Inserisci la Gemini API Key.")
        client = genai.Client(api_key=cfg["gemini_key"])
        response = client.models.generate_content(model=cfg["gemini_model"] or "gemini-2.5-flash", contents=prompt)
        output = (response.text or "").strip()

    if "TITOLO:" not in output or "ARTICOLO:" not in output:
        raise RuntimeError("L'AI non ha rispettato il formato richiesto. Riprova.")
    after_title = output.split("TITOLO:", 1)[1]
    title_part, article_part = after_title.split("ARTICOLO:", 1)
    return clean_title(title_part.strip()), article_part.strip()


def smtp_send_message(msg, cfg):
    smtp_server = cfg["smtp_server"]
    smtp_port = int(cfg["smtp_port"])
    smtp_user = cfg["smtp_user"]
    smtp_password = cfg["smtp_password"]
    if not smtp_server or not smtp_user or not smtp_password:
        raise RuntimeError("Inserisci server SMTP, username SMTP e password SMTP.")
    ssl_context = ssl.create_default_context() if cfg["smtp_verify_ssl"] else ssl._create_unverified_context()
    if cfg["smtp_tls"]:
        with smtplib.SMTP(smtp_server, smtp_port, timeout=30) as server:
            server.ehlo()
            server.starttls(context=ssl_context)
            server.ehlo()
            server.login(smtp_user, smtp_password)
            server.send_message(msg)
    else:
        with smtplib.SMTP_SSL(smtp_server, smtp_port, context=ssl_context, timeout=30) as server:
            server.login(smtp_user, smtp_password)
            server.send_message(msg)


def send_result_email(title, html_article, image_filename, cfg):
    msg = EmailMessage()
    msg["Subject"] = title
    msg["From"] = cfg["smtp_user"]
    msg["To"] = cfg["recipient"] or DESTINATARIO_DEFAULT
    plain_fallback = BeautifulSoup(html_article, "html.parser").get_text("\n", strip=True)
    msg.set_content(plain_fallback)
    msg.add_alternative(html_article, subtype="html")
    if image_filename:
        image_path = ATTACHMENTS_DIR / image_filename
        if image_path.exists():
            data = image_path.read_bytes()
            ext = image_path.suffix.lower().replace(".", "") or "jpeg"
            if ext == "jpg":
                ext = "jpeg"
            msg.add_attachment(data, maintype="image", subtype=ext, filename=image_path.name)
    smtp_send_message(msg, cfg)


def send_confirmation_email_to_sender(to_email, article_title, cfg):
    subject = "Comunicato caricato su Montagne & Paesi"
    body_html = (
        "Buongiorno,<br><br>"
        "abbiamo ricevuto e caricato il comunicato su Montagne & Paesi.<br><br>"
        f"<strong>Titolo:</strong> {article_title}<br><br>"
        "Il contenuto è stato preso in carico dalla redazione.<br><br>"
        "Puoi visitare il sito qui: <a href=\"https://www.montagneepaesi.com\">www.montagneepaesi.com</a><br><br>"
        "<strong>Seguici anche qui:</strong><br>"
        "Facebook: https://www.facebook.com/montagneepaesi/<br>"
        "Instagram: https://www.instagram.com/montagne_e_paesi/<br>"
        "WhatsApp: https://www.whatsapp.com/channel/0029Vb7fcHT8aKvFAuCIfm0c<br>"
        "Telegram: https://t.me/montagnepaesinews<br><br>"
        "Grazie,<br>Redazione Montagne & Paesi"
    )
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg["smtp_user"]
    msg["To"] = to_email
    msg.set_content(BeautifulSoup(body_html, "html.parser").get_text("\n", strip=True))
    msg.add_alternative(body_html, subtype="html")
    smtp_send_message(msg, cfg)


def attachment_stats():
    files = [p for p in ATTACHMENTS_DIR.iterdir() if p.is_file()]
    size = sum(p.stat().st_size for p in files)
    images = [p for p in files if p.suffix.lower() in IMAGE_EXTENSIONS]
    docs = [p for p in files if p.suffix.lower() in DOCUMENT_EXTENSIONS]
    return files, images, docs, size


@app.route("/")
def index():
    cfg = load_config()
    files, images, docs, size = attachment_stats()
    previews = []
    for i, item in enumerate(MAIL_CACHE):
        subject, sender, date = get_email_preview(item.header_msg)
        previews.append({
            "i": i,
            "subject": subject,
            "sender": sender,
            "date": date,
            "size": bytes_to_readable(item.size_bytes),
            "unread": item.is_unread,
        })
    logs = ""
    if LOG_FILE.exists():
        logs = "\n".join(LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()[-120:])
    return render_template("index.html", title=APP_TITLE, cfg=cfg, mails=previews,
                           files=files, images=images, docs=docs, total_size=bytes_to_readable(size), logs=logs)


@app.post("/save-config")
def save_config_route():
    try:
        save_config_from_form(request.form)
        flash("Configurazione salvata. Rimarrà memorizzata nel volume Docker /data.", "success")
    except Exception as e:
        log_exception("Errore salvataggio configurazione", e)
        flash(f"Errore salvataggio configurazione: {e}", "danger")
    return redirect(url_for("index"))


@app.post("/load-mails")
def load_mails_route():
    global MAIL_CACHE
    try:
        cfg = load_config()
        if not cfg["in_server"] or not cfg["in_user"] or not cfg["in_password"]:
            flash("Inserisci server, username e password della casella.", "warning")
            return redirect(url_for("index"))
        MAIL_CACHE = fetch_imap_headers(cfg) if cfg["protocol"] == "IMAP" else fetch_pop3_headers(cfg)
        flash(f"Caricate {len(MAIL_CACHE)} mail.", "success")
        log(f"Caricate {len(MAIL_CACHE)} mail")
    except Exception as e:
        log_exception("Errore caricamento mail", e)
        flash(f"Errore caricamento mail: {type(e).__name__}: {e}", "danger")
    return redirect(url_for("index"))


@app.route("/mail/<int:index>")
def view_mail(index):
    cfg = load_config()
    if index < 0 or index >= len(MAIL_CACHE):
        abort(404)
    try:
        item = MAIL_CACHE[index]
        msg = fetch_full_message_for_item(item, cfg)
        body = extract_body_from_email(msg)
        attachments, images, documents_text = save_attachments_and_extract_text(msg)
        subject, sender, date = get_email_preview(msg)
        flash(f"Allegati salvati nella cartella persistente: {len(attachments)}", "success")
        return render_template("mail.html", title=APP_TITLE, index=index, subject=subject, sender=sender, date=date,
                               body=body, attachments=attachments, images=images, documents_text=documents_text)
    except Exception as e:
        log_exception("Errore apertura mail", e)
        flash(f"Errore apertura mail: {type(e).__name__}: {e}", "danger")
        return redirect(url_for("index"))


@app.post("/generate/<int:index>")
def generate_route(index):
    cfg = load_config()
    if index < 0 or index >= len(MAIL_CACHE):
        abort(404)
    try:
        item = MAIL_CACHE[index]
        msg = fetch_full_message_for_item(item, cfg)
        original_subject, sender, date = get_email_preview(msg)
        email_body = extract_body_from_email(msg)
        attachments, images, documents_text = save_attachments_and_extract_text(msg)
        source_text = f"""
OGGETTO EMAIL:
{original_subject}

MITTENTE:
{sender}

DATA EMAIL:
{date}

TESTO DELLA MAIL:
{email_body}

{documents_text}
""".strip()
        if len(source_text) < 100:
            flash("Testo insufficiente per generare un articolo.", "warning")
            return redirect(url_for("view_mail", index=index))
        article_title, html_article = generate_article(source_text, cfg)
        sender_email = extract_sender_email(msg)
        image_names = [p.name for p in images]
        return render_template("preview.html", title=APP_TITLE, index=index, article_title=article_title,
                               html_article=html_article, image_names=image_names, sender_email=sender_email)
    except Exception as e:
        log_exception("Errore generazione articolo", e)
        flash(f"Errore generazione articolo: {type(e).__name__}: {e}", "danger")
        return redirect(url_for("view_mail", index=index))


@app.post("/send-preview/<int:index>")
def send_preview_route(index):
    cfg = load_config()
    try:
        title = request.form.get("article_title", "").strip()
        html_article = request.form.get("html_article", "").strip()
        image_filename = request.form.get("image_filename", "").strip()
        sender_email = request.form.get("sender_email", "").strip()
        send_confirmation = bool(request.form.get("send_confirmation"))
        if not title or not html_article:
            flash("Titolo e articolo non possono essere vuoti.", "warning")
            return redirect(url_for("index"))
        send_result_email(title, html_article, image_filename, cfg)
        if send_confirmation and sender_email:
            send_confirmation_email_to_sender(sender_email, title, cfg)
        flash("Email articolo inviata correttamente.", "success")
        log(f"Email articolo inviata: {title}")
    except Exception as e:
        log_exception("Errore invio email", e)
        flash(f"Errore invio email: {type(e).__name__}: {e}", "danger")
    return redirect(url_for("index"))


@app.post("/delete-mails")
def delete_mails_route():
    global MAIL_CACHE

    cfg = load_config()

    try:
        selected = [int(x) for x in request.form.getlist("mail_indexes")]
        items = [MAIL_CACHE[i] for i in selected if 0 <= i < len(MAIL_CACHE)]

        if not items:
            flash("Nessuna mail selezionata.", "warning")
            return redirect(url_for("index"))

        delete_messages(items, cfg)

        selected_set = set(selected)
        MAIL_CACHE = [
            item for i, item in enumerate(MAIL_CACHE)
            if i not in selected_set
        ]

        flash(f"Cancellate {len(items)} mail dalla casella.", "success")
        return redirect(url_for("index"))

    except Exception as e:
        log_exception("Errore cancellazione mail", e)
        flash(f"Errore cancellazione mail: {type(e).__name__}: {e}", "danger")
        return redirect(url_for("index"))


@app.post("/clear-attachments")
def clear_attachments_route():
    try:
        count = 0
        for p in ATTACHMENTS_DIR.iterdir():
            if p.is_file() or p.is_symlink():
                p.unlink()
                count += 1
            elif p.is_dir():
                shutil.rmtree(p)
                count += 1
        flash(f"Cartella allegati svuotata. Elementi rimossi: {count}.", "success")
        log(f"Cartella allegati svuotata. Elementi rimossi: {count}")
    except Exception as e:
        log_exception("Errore pulizia allegati", e)
        flash(f"Errore pulizia allegati: {type(e).__name__}: {e}", "danger")
    return redirect(url_for("index"))


@app.route("/attachments/<path:filename>")
def download_attachment(filename):
    safe = Path(filename).name
    path = ATTACHMENTS_DIR / safe
    if not path.exists():
        abort(404)
    return send_from_directory(ATTACHMENTS_DIR, safe, as_attachment=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
