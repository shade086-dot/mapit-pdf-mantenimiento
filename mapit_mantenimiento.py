#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Mapit PDF -> contador de kilómetros y mantenimiento moto.

Uso rápido:
  python mapit_mantenimiento.py importar "InformeMapit_01-06_25-06.pdf"
  python mapit_mantenimiento.py estado
  python mapit_mantenimiento.py engrase --nota "Engrasada después de lavar"
  python mapit_mantenimiento.py revision --tipo aceite --km-actuales 12000

Requisitos:
  pip install pypdf requests
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

try:
    from pypdf import PdfReader
except Exception:  # pragma: no cover
    PdfReader = None
import subprocess
import io
import json

try:
    import requests
except Exception:  # pragma: no cover
    requests = None

try:
    import pdfplumber
except Exception:  # pragma: no cover
    pdfplumber = None


try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build as google_build
    from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload
except Exception:  # pragma: no cover
    service_account = None
    google_build = None
    MediaIoBaseDownload = None
    MediaFileUpload = None

DB_PATH = Path(os.getenv("MAPIT_DB", "moto_maintenance.db"))
PDF_FOLDER = Path(os.getenv("MAPIT_PDF_FOLDER", "pdfs_mapit"))
GOOGLE_DRIVE_FOLDER_ID = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "").strip()
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
GOOGLE_DRIVE_DOWNLOAD_DIR = Path(os.getenv("GOOGLE_DRIVE_DOWNLOAD_DIR", "pdfs_mapit"))
GOOGLE_DRIVE_DB_NAME = os.getenv("GOOGLE_DRIVE_DB_NAME", "moto_maintenance.db")

# Ajustes por defecto. Cámbialos a tu gusto.
CHAIN_GREASE_INTERVAL_KM = float(os.getenv("CHAIN_GREASE_INTERVAL_KM", "1000"))
CHAIN_CLEAN_INTERVAL_KM = float(os.getenv("CHAIN_CLEAN_INTERVAL_KM", "3000"))
OIL_INTERVAL_KM = float(os.getenv("OIL_INTERVAL_KM", "12000"))
TYRE_PRESSURE_INTERVAL_DAYS = int(os.getenv("TYRE_PRESSURE_INTERVAL_DAYS", "15"))

# NTFY opcional: export NTFY_TOPIC="https://ntfy.sh/tu_topic" o "tu_topic"
NTFY_TOPIC = os.getenv("NTFY_TOPIC", "").strip()
NTFY_TITLE = os.getenv("NTFY_TITLE", "🏍️ Mantenimiento moto")


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

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or " ").strip()


def parse_mapit_distance(km_str: str, m_str: str) -> float:
    km = float(km_str.replace(".", "").replace(",", "."))
    metres = float(m_str.replace(".", "").replace(",", "."))
    return km + metres / 1000.0


def parse_duration_to_min(value: str, unit: str, value2: Optional[str] = None) -> int:
    if unit.startswith("h"):
        hours = int(value)
        minutes = int(value2 or 0)
        return hours * 60 + minutes
    return int(value)


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

SUMMARY_RE = re.compile(
    r"Número de trayectos\s+(?P<count>\d+).*?"
    r"Distancia recorrida\s+(?P<km>\d+(?:[\.,]\d+)?)\s+km\s+(?P<m>\d+(?:[\.,]\d+)?)\s+m",
    re.IGNORECASE | re.DOTALL,
)


def read_pdf_text(pdf_path: Path) -> str:
    """Extrae texto del PDF.

    Usa primero `pdftotext` si existe porque los informes Mapit con mapas
    se procesan muchísimo más rápido. Si no está disponible, usa pypdf.
    """
    try:
        result = subprocess.run(
            ["pdftotext", str(pdf_path), "-"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.stdout.strip():
            return result.stdout
    except Exception:
        pass

    if pdfplumber is not None:
        with pdfplumber.open(str(pdf_path)) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)

    if PdfReader is None:
        raise SystemExit("Falta instalar pdfplumber/pypdf o pdftotext. Ejecuta: pip install pdfplumber pypdf")

    reader = PdfReader(str(pdf_path))
    chunks: list[str] = []
    for page in reader.pages:
        chunks.append(page.extract_text() or "")
    return "\n".join(chunks)


def parse_pdf(pdf_path: Path) -> list[Trip]:
    text = read_pdf_text(pdf_path)
    norm = normalize_text(text)
    trips: list[Trip] = []
    for match in TRIP_RE.finditer(norm):
        start_at = datetime.strptime(match.group("start"), "%d/%m/%Y %H:%M").isoformat(timespec="minutes")
        end_at = datetime.strptime(match.group("end"), "%d/%m/%Y %H:%M").isoformat(timespec="minutes")
        duration = int(match.group("min")) + (int(match.group("h") or 0) * 60)
        trips.append(
            Trip(
                trip_number=int(match.group("num")),
                start_at=start_at,
                end_at=end_at,
                start_lon=float(match.group("slon")),
                start_lat=float(match.group("slat")),
                end_lon=float(match.group("elon")),
                end_lat=float(match.group("elat")),
                distance_km=parse_mapit_distance(match.group("km"), match.group("m")),
                duration_min=duration,
                source_pdf=pdf_path.name,
            )
        )
    return trips


def total_trip_km(con: sqlite3.Connection) -> float:
    row = con.execute("SELECT COALESCE(SUM(distance_km), 0) AS km FROM trips").fetchone()
    return float(row["km"])


def import_pdf(pdf_path: Path) -> tuple[int, int, float, float]:
    init_db()
    trips = parse_pdf(pdf_path)
    if not trips:
        raise SystemExit(f"No he encontrado trayectos en: {pdf_path}")

    inserted = 0
    skipped = 0
    added_km = 0.0
    now = datetime.now().isoformat(timespec="seconds")
    with db() as con:
        for t in trips:
            try:
                con.execute(
                    """
                    INSERT INTO trips (
                        trip_key, trip_number, start_at, end_at,
                        start_lon, start_lat, end_lon, end_lat,
                        distance_km, duration_min, source_pdf, imported_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        t.trip_key,
                        t.trip_number,
                        t.start_at,
                        t.end_at,
                        t.start_lon,
                        t.start_lat,
                        t.end_lon,
                        t.end_lat,
                        t.distance_km,
                        t.duration_min,
                        t.source_pdf,
                        now,
                    ),
                )
                inserted += 1
                added_km += t.distance_km
            except sqlite3.IntegrityError:
                skipped += 1
        total = total_trip_km(con)
    return inserted, skipped, added_km, total


def get_last_event(con: sqlite3.Connection, event_type: str) -> Optional[sqlite3.Row]:
    return con.execute(
        "SELECT * FROM maintenance_events WHERE event_type = ? ORDER BY event_at DESC, id DESC LIMIT 1",
        (event_type,),
    ).fetchone()


def km_since_event(con: sqlite3.Connection, event_type: str) -> float:
    last = get_last_event(con, event_type)
    current_total = total_trip_km(con)
    if not last:
        return current_total
    return max(0.0, current_total - float(last["trip_total_km"]))


def add_event(event_type: str, odometer_km: Optional[float], note: str = "") -> None:
    init_db()
    with db() as con:
        total = total_trip_km(con)
        con.execute(
            "INSERT INTO maintenance_events (event_type, event_at, odometer_km, trip_total_km, note) VALUES (?, ?, ?, ?, ?)",
            (event_type, datetime.now().isoformat(timespec="seconds"), odometer_km, total, note),
        )


def recent_trips(con: sqlite3.Connection, limit: int = 5) -> list[sqlite3.Row]:
    return con.execute(
        "SELECT * FROM trips ORDER BY start_at DESC LIMIT ?",
        (limit,),
    ).fetchall()


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

    chain_left = CHAIN_GREASE_INTERVAL_KM - chain_km
    clean_left = CHAIN_CLEAN_INTERVAL_KM - clean_km
    oil_left = OIL_INTERVAL_KM - oil_km

    def line_counter(name: str, km_done: float, interval: float, left: float) -> str:
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
        line_counter("Engrase cadena", chain_km, CHAIN_GREASE_INTERVAL_KM, chain_left),
        line_counter("Limpieza cadena", clean_km, CHAIN_CLEAN_INTERVAL_KM, clean_left),
        line_counter("Aceite/revisión", oil_km, OIL_INTERVAL_KM, oil_left),
        "",
        f"Último engrase: {last_chain['event_at'] if last_chain else 'sin registrar'}",
        f"Última limpieza: {last_clean['event_at'] if last_clean else 'sin registrar'}",
        f"Última revisión aceite: {last_oil['event_at'] if last_oil else 'sin registrar'}",
    ])


def send_ntfy(message: str, title: str = NTFY_TITLE, priority: str = "default") -> None:
    if not NTFY_TOPIC:
        return
    if requests is None:
        print("NTFY_TOPIC configurado, pero falta requests. Ejecuta: pip install requests")
        return
    topic = NTFY_TOPIC
    if not topic.startswith("http"):
        topic = f"https://ntfy.sh/{topic}"
    try:
        requests.post(
            topic,
            data=message.encode("utf-8"),
            headers={"Title": title, "Priority": priority, "Tags": "motorcycle,wrench"},
            timeout=15,
        ).raise_for_status()
    except Exception as exc:
        print(f"No he podido enviar ntfy: {exc}")


def import_folder(folder: Path) -> tuple[int, int, float, float, int]:
    pdfs = sorted(folder.glob("*.pdf"))
    total_inserted = total_skipped = files = 0
    added_km = total_km = 0.0
    for pdf in pdfs:
        ins, skip, km, total = import_pdf(pdf)
        total_inserted += ins
        total_skipped += skip
        added_km += km
        total_km = total
        files += 1
    return total_inserted, total_skipped, added_km, total_km, files


def google_drive_service():
    """Crea cliente de Google Drive con cuenta de servicio.

    GOOGLE_SERVICE_ACCOUNT_JSON puede ser:
      - el JSON completo de la cuenta de servicio
      - una ruta a un .json local
    La carpeta de Drive debe estar compartida con el client_email de esa cuenta.
    """
    if google_build is None or service_account is None:
        raise SystemExit(
            "Faltan dependencias de Google Drive. Ejecuta: "
            "pip install google-api-python-client google-auth"
        )
    if not GOOGLE_SERVICE_ACCOUNT_JSON:
        raise SystemExit("Falta GOOGLE_SERVICE_ACCOUNT_JSON con el JSON o la ruta del service account.")

    if GOOGLE_SERVICE_ACCOUNT_JSON.lstrip().startswith("{"):
        info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
        creds = service_account.Credentials.from_service_account_info(
            info, scopes=["https://www.googleapis.com/auth/drive"]
        )
    else:
        creds = service_account.Credentials.from_service_account_file(
            GOOGLE_SERVICE_ACCOUNT_JSON, scopes=["https://www.googleapis.com/auth/drive"]
        )
    return google_build("drive", "v3", credentials=creds, cache_discovery=False)


def drive_list_files(service, folder_id: str, mime_type: Optional[str] = None, name: Optional[str] = None) -> list[dict]:
    parts = [f"'{folder_id}' in parents", "trashed = false"]
    if mime_type:
        parts.append(f"mimeType = '{mime_type}'")
    if name:
        safe_name = name.replace("'", "\\'")
        parts.append(f"name = '{safe_name}'")
    q = " and ".join(parts)
    files: list[dict] = []
    page_token = None
    while True:
        resp = service.files().list(
            q=q,
            spaces="drive",
            fields="nextPageToken, files(id, name, mimeType, modifiedTime, size, md5Checksum)",
            pageToken=page_token,
            pageSize=1000,
            orderBy="modifiedTime desc",
        ).execute()
        files.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return files


def drive_download_file(service, file_id: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = service.files().get_media(fileId=file_id)
    with destination.open("wb") as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()


def drive_upload_or_update_file(service, folder_id: str, local_path: Path, drive_name: str, mime_type: str) -> str:
    existing = drive_list_files(service, folder_id, name=drive_name)
    media = MediaFileUpload(str(local_path), mimetype=mime_type, resumable=False)
    if existing:
        file_id = existing[0]["id"]
        service.files().update(fileId=file_id, media_body=media, fields="id").execute()
        return file_id
    metadata = {"name": drive_name, "parents": [folder_id]}
    created = service.files().create(body=metadata, media_body=media, fields="id").execute()
    return created["id"]


def sync_drive_pdfs(service, folder_id: str, local_folder: Path) -> tuple[int, int]:
    """Descarga PDFs de Drive a local_folder. Devuelve (descargados, ya_existian)."""
    local_folder.mkdir(parents=True, exist_ok=True)
    pdfs = drive_list_files(service, folder_id, mime_type="application/pdf")
    downloaded = skipped = 0
    for f in pdfs:
        # Prefijo con ID para evitar choques si dos PDFs tienen el mismo nombre.
        safe_name = re.sub(r"[^A-Za-z0-9_.() -]+", "_", f["name"])
        dest = local_folder / f"{f['id']}_{safe_name}"
        expected_size = int(f.get("size") or 0)
        if dest.exists() and (expected_size == 0 or dest.stat().st_size == expected_size):
            skipped += 1
            continue
        drive_download_file(service, f["id"], dest)
        downloaded += 1
    return downloaded, skipped


def import_drive_folder(send_notification: bool = False) -> str:
    """Sincroniza PDF + DB con una carpeta de Google Drive e importa rutas.

    Pensado para Render Cron: como el disco puede ser temporal, baja primero la DB
    de Drive, procesa los PDF y vuelve a subir la DB actualizada.
    """
    if not GOOGLE_DRIVE_FOLDER_ID:
        raise SystemExit("Falta GOOGLE_DRIVE_FOLDER_ID con el ID de la carpeta de Google Drive.")
    service = google_drive_service()

    # Si ya hay DB en Drive, la bajamos antes para mantener histórico/deduplicación.
    db_files = drive_list_files(service, GOOGLE_DRIVE_FOLDER_ID, name=GOOGLE_DRIVE_DB_NAME)
    if db_files:
        drive_download_file(service, db_files[0]["id"], DB_PATH)

    downloaded, already_local = sync_drive_pdfs(service, GOOGLE_DRIVE_FOLDER_ID, GOOGLE_DRIVE_DOWNLOAD_DIR)
    inserted, skipped, added, total, files = import_folder(GOOGLE_DRIVE_DOWNLOAD_DIR)

    # Subimos la DB al Drive para que el siguiente cron no duplique nada aunque Render sea efímero.
    if DB_PATH.exists():
        drive_upload_or_update_file(
            service,
            GOOGLE_DRIVE_FOLDER_ID,
            DB_PATH,
            GOOGLE_DRIVE_DB_NAME,
            "application/vnd.sqlite3",
        )

    msg = (
        "🏍️ Google Drive Mapit procesado\n"
        f"PDFs descargados: {downloaded}\n"
        f"PDFs ya locales: {already_local}\n"
        f"PDFs revisados: {files}\n"
        f"Nuevos trayectos: {inserted}\n"
        f"Duplicados ignorados: {skipped}\n"
        f"Km añadidos: {added:.3f}\n"
        f"Km totales: {total:.3f}\n\n"
        f"{build_status_text()}"
    )
    if send_notification:
        send_ntfy(msg, priority="high" if "TOCA ya" in msg else "default")
    return msg


def main() -> None:
    parser = argparse.ArgumentParser(description="Importa PDFs de Mapit y controla mantenimiento de la moto.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_imp = sub.add_parser("importar", help="Importa un PDF de Mapit")
    p_imp.add_argument("pdf", type=Path)
    p_imp.add_argument("--ntfy", action="store_true", help="Enviar resumen por ntfy si NTFY_TOPIC está configurado")

    p_folder = sub.add_parser("importar-carpeta", help="Importa todos los PDFs de una carpeta")
    p_folder.add_argument("folder", nargs="?", type=Path, default=PDF_FOLDER)
    p_folder.add_argument("--ntfy", action="store_true")

    p_drive = sub.add_parser("importar-drive", help="Descarga PDFs desde una carpeta de Google Drive, importa y sube la DB actualizada")
    p_drive.add_argument("--ntfy", action="store_true")

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
        inserted, skipped, added, total = import_pdf(args.pdf)
        msg = f"🏍️ Mapit procesado\nPDF: {args.pdf.name}\nNuevos trayectos: {inserted}\nDuplicados ignorados: {skipped}\nKm añadidos: {added:.3f}\nKm totales: {total:.3f}\n\n{build_status_text()}"
        print(msg)
        if args.ntfy:
            send_ntfy(msg, priority="high" if "TOCA ya" in msg else "default")

    elif args.cmd == "importar-carpeta":
        inserted, skipped, added, total, files = import_folder(args.folder)
        msg = f"🏍️ Carpeta Mapit procesada\nPDFs revisados: {files}\nNuevos trayectos: {inserted}\nDuplicados ignorados: {skipped}\nKm añadidos: {added:.3f}\nKm totales: {total:.3f}\n\n{build_status_text()}"
        print(msg)
        if args.ntfy:
            send_ntfy(msg, priority="high" if "TOCA ya" in msg else "default")

    elif args.cmd == "importar-drive":
        print(import_drive_folder(send_notification=args.ntfy))

    elif args.cmd == "estado":
        print(build_status_text())

    elif args.cmd == "engrase":
        add_event("engrase_cadena", args.km_actuales, args.nota)
        print("✅ Engrase de cadena registrado.\n")
        print(build_status_text())

    elif args.cmd == "limpieza-cadena":
        add_event("limpieza_cadena", args.km_actuales, args.nota)
        print("✅ Limpieza de cadena registrada.\n")
        print(build_status_text())

    elif args.cmd == "revision":
        # De momento guardamos la revisión principal como 'aceite'. El texto libre queda en nota.
        add_event("aceite", args.km_actuales, f"{args.tipo}. {args.nota}".strip())
        print("✅ Revisión registrada.\n")
        print(build_status_text())


if __name__ == "__main__":
    main()
