from __future__ import annotations

import email
import imaplib
from datetime import datetime
from email.message import Message
from html import unescape

from . import github_storage
from .commands import process_command
from .config import (
    ARCHIVE_EMAIL_AFTER_SUCCESS,
    GMAIL_ADDRESS,
    GMAIL_APP_PASSWORD,
    GMAIL_MAILBOX,
    MAPIT_COMMAND_SEARCH,
    MAPIT_REPORT_SEARCH,
    MARK_EMAIL_AS_SEEN,
    NTFY_NOTIFY_EMPTY,
)
from .database import db, init_db
from .downloader import download_pdf_from_link, find_mapit_report_links
from .maintenance import build_status_text, import_pdf, total_trip_km
from .notifications import send_ntfy
from .reminders import append_footer, smart_reminders_text


def extract_text_and_html(msg: Message) -> str:
    parts: list[str] = []
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype in ("text/plain", "text/html"):
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    parts.append(payload.decode(charset, errors="replace"))
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            parts.append(payload.decode(msg.get_content_charset() or "utf-8", errors="replace"))
    return "\n".join(parts)


def decode_subject(msg: Message) -> str:
    return str(email.header.make_header(email.header.decode_header(msg.get("Subject", ""))))


def already_processed(uid: str, kind: str) -> bool:
    init_db()
    key = f"{kind}:{uid}"
    with db() as con:
        return con.execute("SELECT 1 FROM processed_emails WHERE message_uid = ?", (key,)).fetchone() is not None


def mark_processed(uid: str, kind: str, subject: str, pdf_name: str = "", inserted: int = 0, added: float = 0.0, command: str = "") -> None:
    init_db()
    key = f"{kind}:{uid}"
    with db() as con:
        con.execute(
            """
            INSERT OR REPLACE INTO processed_emails
            (message_uid, kind, subject, processed_at, pdf_name, inserted_trips, added_km, command)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (key, kind, subject, datetime.now().isoformat(timespec="seconds"), pdf_name, inserted, added, command),
        )


def connect() -> imaplib.IMAP4_SSL:
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        raise SystemExit("Faltan GMAIL_ADDRESS y/o GMAIL_APP_PASSWORD.")
    imap = imaplib.IMAP4_SSL("imap.gmail.com")
    imap.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
    imap.select(GMAIL_MAILBOX)
    return imap


def search_uids(imap: imaplib.IMAP4_SSL, query: str, max_emails: int) -> list[bytes]:
    status, data = imap.uid("search", None, query)
    if status != "OK":
        raise RuntimeError(f"Gmail search falló: {status} {data}")
    return data[0].split()[-max_emails:]


def fetch_message(imap: imaplib.IMAP4_SSL, uid: bytes) -> tuple[str, Message] | None:
    uid_text = uid.decode("ascii")
    status, msg_data = imap.uid("fetch", uid_text, "(RFC822)")
    if status != "OK" or not msg_data or not msg_data[0]:
        return None
    raw = msg_data[0][1]
    return uid_text, email.message_from_bytes(raw)


def finish_email(imap: imaplib.IMAP4_SSL, uid: str) -> None:
    if MARK_EMAIL_AS_SEEN:
        imap.uid("store", uid, "+FLAGS", "(\\Seen)")
    if ARCHIVE_EMAIL_AFTER_SUCCESS:
        imap.uid("store", uid, "+FLAGS", "(\\Seen)")
        imap.uid("store", uid, "+X-GM-LABELS", "(MapitProcesado)")
        imap.uid("store", uid, "+FLAGS", "(\\Deleted)")


def process_reports(imap: imaplib.IMAP4_SSL, max_emails: int) -> tuple[int, int, int, float, list[str]]:
    uids = search_uids(imap, MAPIT_REPORT_SEARCH, max_emails)
    processed = total_inserted = total_skipped = 0
    total_added = 0.0
    messages: list[str] = []
    for uid_bytes in uids:
        fetched = fetch_message(imap, uid_bytes)
        if not fetched:
            continue
        uid, msg = fetched
        if already_processed(uid, "report"):
            continue
        subject = decode_subject(msg)
        body = extract_text_and_html(msg)
        links = find_mapit_report_links(body)
        if not links:
            continue
        pdf_path = download_pdf_from_link(links[0])
        inserted, skipped, added, _total = import_pdf(pdf_path)
        mark_processed(uid, "report", subject, pdf_path.name, inserted, added)
        total_inserted += inserted
        total_skipped += skipped
        total_added += added
        processed += 1
        messages.append(f"- {pdf_path.name}: +{inserted} trayectos, {added:.3f} km")
        finish_email(imap, uid)
    return processed, total_inserted, total_skipped, total_added, messages


def process_commands(imap: imaplib.IMAP4_SSL, max_emails: int, send_notification: bool) -> tuple[int, list[str]]:
    uids = search_uids(imap, MAPIT_COMMAND_SEARCH, max_emails)
    processed = 0
    summaries: list[str] = []
    for uid_bytes in uids:
        fetched = fetch_message(imap, uid_bytes)
        if not fetched:
            continue
        uid, msg = fetched
        if already_processed(uid, "command"):
            continue
        subject = decode_subject(msg)
        body = unescape(extract_text_and_html(msg))
        handled, response, command = process_command(subject, body)
        if not handled:
            continue
        mark_processed(uid, "command", subject, command=command or "")
        processed += 1
        summaries.append(f"- {subject}: {command}")
        if send_notification:
            send_ntfy(response, priority="high" if "TOCA ya" in response or "⚠️" in response else "default")
        finish_email(imap, uid)
    return processed, summaries


def import_from_gmail(send_notification: bool = False, max_emails: int = 10) -> str:
    github_storage.download_db()
    init_db()
    imap = connect()
    reports_count = commands_count = 0
    try:
        reports_count, total_inserted, total_skipped, total_added, report_messages = process_reports(imap, max_emails)
        commands_count, command_messages = process_commands(imap, max_emails, send_notification)
        if ARCHIVE_EMAIL_AFTER_SUCCESS:
            imap.expunge()
    finally:
        imap.logout()

    if reports_count or commands_count:
        github_storage.upload_db()

    with db() as con:
        current_total = total_trip_km(con)

    blocks: list[str]
    if not reports_count and not commands_count:
        reminder = smart_reminders_text()
        if send_notification and (NTFY_NOTIFY_EMPTY or reminder):
            send_ntfy(append_footer("🏍️ Mapit Gmail\nNo hay informes ni comandos nuevos."), priority="default")
        return "🏍️ Mapit Gmail\nNo hay informes ni comandos nuevos."

    blocks = ["🏍️ Mapit V4 procesado"]
    if reports_count:
        blocks += [
            "",
            "📄 Informes Mapit",
            f"Emails procesados: {reports_count}",
            f"Nuevos trayectos: {total_inserted}",
            f"Duplicados ignorados: {total_skipped}",
            f"Km añadidos: {total_added:.3f}",
            f"Km totales: {current_total:.3f}",
            "",
            *report_messages,
            "",
            build_status_text(),
        ]
    if commands_count:
        blocks += ["", "📧 Comandos procesados", *command_messages]
    msg = append_footer("\n".join(blocks))
    if send_notification and reports_count:
        send_ntfy(msg, priority="high" if "TOCA ya" in msg or "⚠️" in msg else "default")
    return msg
