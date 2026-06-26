#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Mapit Gmail/PDF -> contador de kilómetros y mantenimiento moto.

Comandos principales:
  python mapit_mantenimiento.py importar InformeMapit.pdf
  python mapit_mantenimiento.py importar-gmail --ntfy
  python mapit_mantenimiento.py estado
  python mapit_mantenimiento.py engrase --nota "Engrase cadena"
"""
from __future__ import annotations

import argparse
import base64
import email
import hashlib
import imaplib
import json
import os
import re
import sqlite3
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime
from email.message import Message
from html import unescape
from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urlparse, parse_qs

try:
    import requests
except Exception:  # pragma: no cover
    requests = None

try:
    import pdfplumber
except Exception:  # pragma: no cover
    pdfplumber = None

try:
    from pypdf import PdfReader
except Exception:  # pragma: no cover
    PdfReader = None

DB_PATH = Path(os.getenv("MAPIT_DB", "moto_maintenance.db"))
DOWNLOAD_DIR = Path(os.getenv("MAPIT_DOWNLOAD_DIR", "downloads_mapit"))

# Gmail IMAP. Recomendado usar App Password, no tu contraseña normal.
GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS", "").strip()
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "").strip()
GMAIL_MAILBOX = os.getenv("GMAIL_MAILBOX", "INBOX").strip() or "INBOX"
MAPIT_EMAIL_SEARCH = os.getenv("MAPIT_EMAIL_SEARCH", '(UNSEEN FROM "mapit")')
MARK_EMAIL_AS_SEEN = os.getenv("MARK_EMAIL_AS_SEEN", "1") == "1"
ARCHIVE_EMAIL_AFTER_SUCCESS = os.getenv("ARCHIVE_EMAIL_AFTER_SUCCESS", "0") == "1"

# Persistencia opcional en GitHub para Render Cron.
# Si no se configura, usa la DB local.
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()
GITHUB_REPO = os.getenv("GITHUB_REPO", "").strip()  # usuario/repo
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main").strip() or "main"
GITHUB_DB_PATH = os.getenv("GITHUB_DB_PATH", "data/moto_maintenance.db").strip()

# Ajustes mantenimiento.
CHAIN_GREASE_INTERVAL_KM = float(os.getenv("CHAIN_GREASE_INTERVAL_KM", "1000"))
CHAIN_CLEAN_INTERVAL_KM = float(os.getenv("CHAIN_CLEAN_INTERVAL_KM", "3000"))
OIL_INTERVAL_KM = float(os.getenv("OIL_INTERVAL_KM", "12000"))

# ntfy opcional.
NTFY_TOPIC = os.getenv("NTFY_TOPIC", "").strip()
NTFY_TITLE = os.getenv("NTFY_TITLE", "🏍️ Mantenimiento CB750")


@dataclass(frozen=True)
class Trip:
    trip_number: int
    start_at: str
    end_at: str
    start_lon: float
    start_lat: float
    end_lon: float
    end_lat: float
    distance_km: float
    duration_min: int
    source_pdf: str

    @property
    def trip_key(self) -> str:
        raw = "|".join([
            self.start_at,
            self.end_at,
            f"{self.start_lon:.6f}",
            f"{self.start_lat:.6f}",
            f"{self.end_lon:.6f}",
            f"{self.end_lat:.6f}",
            f"{self.distance_km:.3f}",
        ])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db() -> None:
    with db() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS trips (
                trip_key TEXT PRIMARY KEY,
                trip_number INTEGER,
                start_at TEXT NOT NULL,
                end_at TEXT NOT NULL,
                start_lon REAL,
                start_lat REAL,
                end_lon REAL,
                end_lat REAL,
                distance_km REAL NOT NULL,
                duration_min INTEGER,
                source_pdf TEXT,
                imported_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS maintenance_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                event_at TEXT NOT NULL,
                odometer_km REAL,
                trip_total_km REAL NOT NULL,
                note TEXT
            );

            CREATE TABLE IF NOT EXISTS processed_emails (
                message_uid TEXT PRIMARY KEY,
                subject TEXT,
                processed_at TEXT NOT NULL,
                pdf_name TEXT,
                inserted_trips INTEGER,
                added_km REAL
            );
            """
        )


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or " ").strip()


def parse_mapit_distance(km_str: str, m_str: str) -> float:
    km = float(km_str.replace(".", "").replace(",", "."))
    metres = float(m_str.replace(".", "").replace(",", "."))
    return km + metres / 1000.0


TRIP_RE = re.compile(
    r"Trayecto\s+(?P<num>\d+).*?"
    r"Inicio estimado\s+(?P<start>\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}).*?"
    r"Final\s+(?P<end>\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}).*?"
    r"Posici[oó]n inicial\s+(?P<slon>-?\d+(?:\.\d+)?)\s+(?P<slat>-?\d+(?:\.\d+)?).*?"
    r"Posici[oó]n final\s+(?P<elon>-?\d+(?:\.\d+)?)\s+(?P<elat>-?\d+(?:\.\d+)?).*?"
    r"Distancia recorrida\s+(?P<km>\d+(?:[\.,]\d+)?)\s+km\s+(?P<m>\d+(?:[\.,]\d+)?)\s+m.*?"
    r"Duraci[oó]n\s+(?:(?P<h>\d+)\s+h\s+)?(?P<min>\d+)\s+min",
    re.IGNORECASE | re.DOTALL,
)


def read_pdf_text(pdf_path: Path) -> str:
    try:
        result = subprocess.run(["pdftotext", str(pdf_path), "-"], check=True, capture_output=True, text=True, timeout=40)
        if result.stdout.strip():
            return result.stdout
    except Exception:
        pass

    if pdfplumber is not None:
        with pdfplumber.open(str(pdf_path)) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)

    if PdfReader is None:
        raise SystemExit("Falta instalar pdfplumber/pypdf. Ejecuta: pip install -r requirements.txt")
    reader = PdfReader(str(pdf_path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def parse_pdf(pdf_path: Path) -> list[Trip]:
    norm = normalize_text(read_pdf_text(pdf_path))
    trips: list[Trip] = []
    for m in TRIP_RE.finditer(norm):
        start_at = datetime.strptime(m.group("start"), "%d/%m/%Y %H:%M").isoformat(timespec="minutes")
        end_at = datetime.strptime(m.group("end"), "%d/%m/%Y %H:%M").isoformat(timespec="minutes")
        trips.append(Trip(
            trip_number=int(m.group("num")),
            start_at=start_at,
            end_at=end_at,
            start_lon=float(m.group("slon")),
            start_lat=float(m.group("slat")),
            end_lon=float(m.group("elon")),
            end_lat=float(m.group("elat")),
            distance_km=parse_mapit_distance(m.group("km"), m.group("m")),
            duration_min=int(m.group("min")) + int(m.group("h") or 0) * 60,
            source_pdf=pdf_path.name,
        ))
    return trips


def total_trip_km(con: sqlite3.Connection) -> float:
    return float(con.execute("SELECT COALESCE(SUM(distance_km), 0) AS km FROM trips").fetchone()["km"])


def import_pdf(pdf_path: Path) -> tuple[int, int, float, float]:
    init_db()
    trips = parse_pdf(pdf_path)
    if not trips:
        raise SystemExit(f"No he encontrado trayectos en: {pdf_path}")
    inserted = skipped = 0
    added_km = 0.0
    now = datetime.now().isoformat(timespec="seconds")
    with db() as con:
        for t in trips:
            try:
                con.execute(
                    """
                    INSERT INTO trips (trip_key, trip_number, start_at, end_at, start_lon, start_lat, end_lon, end_lat,
                                       distance_km, duration_min, source_pdf, imported_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (t.trip_key, t.trip_number, t.start_at, t.end_at, t.start_lon, t.start_lat, t.end_lon, t.end_lat,
                     t.distance_km, t.duration_min, t.source_pdf, now),
                )
                inserted += 1
                added_km += t.distance_km
            except sqlite3.IntegrityError:
                skipped += 1
        total = total_trip_km(con)
    return inserted, skipped, added_km, total


def get_last_event(con: sqlite3.Connection, event_type: str) -> Optional[sqlite3.Row]:
    return con.execute("SELECT * FROM maintenance_events WHERE event_type = ? ORDER BY event_at DESC, id DESC LIMIT 1", (event_type,)).fetchone()


def km_since_event(con: sqlite3.Connection, event_type: str) -> float:
    last = get_last_event(con, event_type)
    current = total_trip_km(con)
    return current if not last else max(0.0, current - float(last["trip_total_km"]))


def add_event(event_type: str, odometer_km: Optional[float], note: str = "") -> None:
    init_db()
    with db() as con:
        con.execute(
            "INSERT INTO maintenance_events (event_type, event_at, odometer_km, trip_total_km, note) VALUES (?, ?, ?, ?, ?)",
            (event_type, datetime.now().isoformat(timespec="seconds"), odometer_km, total_trip_km(con), note),
        )


def build_status_text() -> str:
    init_db()
    with db() as con:
        total_km = total_trip_km(con)
        trips_count = con.execute("SELECT COUNT(*) AS n FROM trips").fetchone()["n"]
        chain_km = km_since_event(con, "engrase_cadena")
        clean_km = km_since_event(con, "limpieza_cadena")
        oil_km = km_since_event(con, "aceite")
        last_chain = get_last_event(con, "engrase_cadena")
        last_clean = get_last_event(con, "limpieza_cadena")
        last_oil = get_last_event(con, "aceite")

    def counter(name: str, km_done: float, interval: float) -> str:
        left = interval - km_done
        if left <= 0:
            return f"⚠️ {name}: {km_done:.0f}/{interval:.0f} km — TOCA ya"
        if left <= interval * 0.15:
            return f"🔶 {name}: {km_done:.0f}/{interval:.0f} km — quedan {left:.0f} km"
        return f"✅ {name}: {km_done:.0f}/{interval:.0f} km — quedan {left:.0f} km"

    return "\n".join([
        "🏍️ Estado mantenimiento Mapit",
        f"Trayectos guardados: {trips_count}",
        f"Km totales importados: {total_km:.3f} km",
        "",
        counter("Engrase cadena", chain_km, CHAIN_GREASE_INTERVAL_KM),
        counter("Limpieza cadena", clean_km, CHAIN_CLEAN_INTERVAL_KM),
        counter("Aceite/revisión", oil_km, OIL_INTERVAL_KM),
        "",
        f"Último engrase: {last_chain['event_at'] if last_chain else 'sin registrar'}",
        f"Última limpieza: {last_clean['event_at'] if last_clean else 'sin registrar'}",
        f"Última revisión aceite: {last_oil['event_at'] if last_oil else 'sin registrar'}",
    ])


def send_ntfy(message: str, title: str = NTFY_TITLE, priority: str = "default") -> None:
    if not NTFY_TOPIC:
        return
    if requests is None:
        print("Falta requests para ntfy")
        return
    topic = NTFY_TOPIC if NTFY_TOPIC.startswith("http") else f"https://ntfy.sh/{NTFY_TOPIC}"
    try:
        requests.post(topic, data=message.encode("utf-8"), headers={"Title": title, "Priority": priority, "Tags": "motorcycle,wrench"}, timeout=20).raise_for_status()
    except Exception as exc:
        print(f"No he podido enviar ntfy: {exc}")


def github_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}


def github_enabled() -> bool:
    return bool(GITHUB_TOKEN and GITHUB_REPO and requests is not None)


def github_download_db() -> None:
    if not github_enabled():
        return
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_DB_PATH}"
    r = requests.get(url, headers=github_headers(), params={"ref": GITHUB_BRANCH}, timeout=30)
    if r.status_code == 404:
        return
    r.raise_for_status()
    content = base64.b64decode(r.json()["content"])
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    DB_PATH.write_bytes(content)


def github_upload_db(commit_message: str = "Actualiza DB mantenimiento Mapit") -> None:
    if not github_enabled() or not DB_PATH.exists():
        return
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_DB_PATH}"
    get = requests.get(url, headers=github_headers(), params={"ref": GITHUB_BRANCH}, timeout=30)
    sha = get.json().get("sha") if get.status_code == 200 else None
    if get.status_code not in (200, 404):
        get.raise_for_status()
    payload = {
        "message": commit_message,
        "content": base64.b64encode(DB_PATH.read_bytes()).decode("ascii"),
        "branch": GITHUB_BRANCH,
    }
    if sha:
        payload["sha"] = sha
    r = requests.put(url, headers=github_headers(), json=payload, timeout=30)
    r.raise_for_status()


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


def find_mapit_report_links(text: str) -> list[str]:
    text = unescape(text)
    candidates = re.findall(r'https?://[^\s"\'<>]+', text)
    links: list[str] = []
    for raw in candidates:
        url = raw.rstrip(").,;]")
        decoded = unquote(url)
        if "route-report" in decoded or "InformeMapit" in decoded or "pdfUrl=" in decoded:
            links.append(url)
    # Quita duplicados preservando orden
    seen = set()
    out = []
    for l in links:
        if l not in seen:
            seen.add(l)
            out.append(l)
    return out


def filename_from_url(url: str, fallback: str = "InformeMapit.pdf") -> str:
    decoded = unquote(url)
    m = re.search(r"(InformeMapit_[^/?#]+\.pdf)", decoded)
    if m:
        return re.sub(r"[^A-Za-z0-9_.() -]+", "_", m.group(1))
    name = Path(urlparse(decoded).path).name
    if name.lower().endswith(".pdf"):
        return re.sub(r"[^A-Za-z0-9_.() -]+", "_", name)
    return fallback


def download_pdf_from_link(link: str, dest_dir: Path) -> Path:
    if requests is None:
        raise SystemExit("Falta requests. Ejecuta: pip install -r requirements.txt")
    dest_dir.mkdir(parents=True, exist_ok=True)
    filename = filename_from_url(link)
    dest = dest_dir / filename
    headers = {"User-Agent": "Mozilla/5.0 mapit-maintenance/1.0"}
    r = requests.get(link, headers=headers, allow_redirects=True, timeout=60)
    r.raise_for_status()
    content_type = r.headers.get("content-type", "").lower()
    if "pdf" not in content_type and not r.content.startswith(b"%PDF"):
        # Algunos redirects intermedios devuelven HTML. Intentamos encontrar otro enlace dentro.
        html = r.text
        links = find_mapit_report_links(html)
        if links and links[0] != link:
            return download_pdf_from_link(links[0], dest_dir)
        raise RuntimeError(f"El enlace no devolvió un PDF. Content-Type={content_type}")
    dest.write_bytes(r.content)
    return dest


def already_processed(uid: str) -> bool:
    init_db()
    with db() as con:
        return con.execute("SELECT 1 FROM processed_emails WHERE message_uid = ?", (uid,)).fetchone() is not None


def mark_processed(uid: str, subject: str, pdf_name: str, inserted: int, added: float) -> None:
    init_db()
    with db() as con:
        con.execute(
            "INSERT OR REPLACE INTO processed_emails (message_uid, subject, processed_at, pdf_name, inserted_trips, added_km) VALUES (?, ?, ?, ?, ?, ?)",
            (uid, subject, datetime.now().isoformat(timespec="seconds"), pdf_name, inserted, added),
        )


def import_from_gmail(send_notification: bool = False, max_emails: int = 10) -> str:
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        raise SystemExit("Faltan GMAIL_ADDRESS y/o GMAIL_APP_PASSWORD.")
    github_download_db()
    init_db()

    imap = imaplib.IMAP4_SSL("imap.gmail.com")
    imap.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
    imap.select(GMAIL_MAILBOX)
    status, data = imap.uid("search", None, MAPIT_EMAIL_SEARCH)
    if status != "OK":
        raise RuntimeError(f"Gmail search falló: {status} {data}")
    uids = data[0].split()[-max_emails:]

    total_inserted = total_skipped = 0
    total_added = 0.0
    processed_count = 0
    messages: list[str] = []

    for uid_bytes in uids:
        uid = uid_bytes.decode("ascii")
        if already_processed(uid):
            continue
        status, msg_data = imap.uid("fetch", uid, "(RFC822)")
        if status != "OK" or not msg_data or not msg_data[0]:
            continue
        raw = msg_data[0][1]
        msg = email.message_from_bytes(raw)
        subject = email.header.make_header(email.header.decode_header(msg.get("Subject", ""))).__str__()
        body = extract_text_and_html(msg)
        links = find_mapit_report_links(body)
        if not links:
            continue
        pdf_path = download_pdf_from_link(links[0], DOWNLOAD_DIR)
        inserted, skipped, added, total = import_pdf(pdf_path)
        mark_processed(uid, subject, pdf_path.name, inserted, added)
        total_inserted += inserted
        total_skipped += skipped
        total_added += added
        processed_count += 1
        messages.append(f"- {pdf_path.name}: +{inserted} trayectos, {added:.3f} km")
        if MARK_EMAIL_AS_SEEN:
            imap.uid("store", uid, "+FLAGS", "(\\Seen)")
        if ARCHIVE_EMAIL_AFTER_SUCCESS:
            imap.uid("store", uid, "+FLAGS", "(\\Seen)")
            imap.uid("store", uid, "+X-GM-LABELS", "(MapitProcesado)")
            imap.uid("store", uid, "+FLAGS", "(\\Deleted)")

    if ARCHIVE_EMAIL_AFTER_SUCCESS:
        imap.expunge()
    imap.logout()

    if processed_count:
        github_upload_db()
    with db() as con:
        current_total = total_trip_km(con)

    if not processed_count:
        msg = "🏍️ Mapit Gmail\nNo hay informes nuevos para procesar."
    else:
        msg = "\n".join([
            "🏍️ Mapit Gmail procesado",
            f"Emails procesados: {processed_count}",
            f"Nuevos trayectos: {total_inserted}",
            f"Duplicados ignorados: {total_skipped}",
            f"Km añadidos: {total_added:.3f}",
            f"Km totales: {current_total:.3f}",
            "",
            *messages,
            "",
            build_status_text(),
        ])
    if send_notification and (processed_count or os.getenv("NTFY_NOTIFY_EMPTY", "0") == "1"):
        send_ntfy(msg, priority="high" if "TOCA ya" in msg else "default")
    return msg


def main() -> None:
    parser = argparse.ArgumentParser(description="Importa informes Mapit desde PDF o Gmail y controla mantenimiento.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_imp = sub.add_parser("importar", help="Importa un PDF de Mapit")
    p_imp.add_argument("pdf", type=Path)
    p_imp.add_argument("--ntfy", action="store_true")

    p_gmail = sub.add_parser("importar-gmail", help="Busca emails nuevos de Mapit, descarga el PDF e importa rutas")
    p_gmail.add_argument("--ntfy", action="store_true")
    p_gmail.add_argument("--max-emails", type=int, default=10)

    sub.add_parser("estado", help="Muestra estado de mantenimiento")

    p_eng = sub.add_parser("engrase", help="Registra engrase de cadena")
    p_eng.add_argument("--km-actuales", type=float, default=None)
    p_eng.add_argument("--nota", default="")

    p_clean = sub.add_parser("limpieza-cadena", help="Registra limpieza de cadena")
    p_clean.add_argument("--km-actuales", type=float, default=None)
    p_clean.add_argument("--nota", default="")

    p_oil = sub.add_parser("revision", help="Registra revisión/cambio aceite")
    p_oil.add_argument("--tipo", default="aceite")
    p_oil.add_argument("--km-actuales", type=float, default=None)
    p_oil.add_argument("--nota", default="")

    args = parser.parse_args()

    if args.cmd == "importar":
        github_download_db()
        inserted, skipped, added, total = import_pdf(args.pdf)
        github_upload_db()
        msg = f"🏍️ Mapit procesado\nPDF: {args.pdf.name}\nNuevos trayectos: {inserted}\nDuplicados ignorados: {skipped}\nKm añadidos: {added:.3f}\nKm totales: {total:.3f}\n\n{build_status_text()}"
        print(msg)
        if args.ntfy:
            send_ntfy(msg, priority="high" if "TOCA ya" in msg else "default")
    elif args.cmd == "importar-gmail":
        print(import_from_gmail(send_notification=args.ntfy, max_emails=args.max_emails))
    elif args.cmd == "estado":
        github_download_db()
        print(build_status_text())
    elif args.cmd == "engrase":
        github_download_db(); add_event("engrase_cadena", args.km_actuales, args.nota); github_upload_db()
        print("✅ Engrase registrado.\n" + build_status_text())
    elif args.cmd == "limpieza-cadena":
        github_download_db(); add_event("limpieza_cadena", args.km_actuales, args.nota); github_upload_db()
        print("✅ Limpieza registrada.\n" + build_status_text())
    elif args.cmd == "revision":
        github_download_db(); add_event("aceite", args.km_actuales, f"{args.tipo}. {args.nota}".strip()); github_upload_db()
        print("✅ Revisión registrada.\n" + build_status_text())


if __name__ == "__main__":
    main()
