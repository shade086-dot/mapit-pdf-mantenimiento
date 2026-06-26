#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mapit Gmail/PDF -> contador de kilómetros y mantenimiento moto. V3."""
from __future__ import annotations
import argparse, base64, email, hashlib, imaplib, os, re, sqlite3, subprocess
from dataclasses import dataclass
from datetime import datetime
from email.message import Message
from html import unescape
from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urlparse

try:
    import requests
except Exception:
    requests = None
try:
    import pdfplumber
except Exception:
    pdfplumber = None
try:
    from pypdf import PdfReader
except Exception:
    PdfReader = None

DB_PATH = Path(os.getenv("MAPIT_DB", "moto_maintenance.db"))
DOWNLOAD_DIR = Path(os.getenv("MAPIT_DOWNLOAD_DIR", "downloads_mapit"))
GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS", "").strip()
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "").strip()
GMAIL_MAILBOX = os.getenv("GMAIL_MAILBOX", "INBOX").strip() or "INBOX"
MAPIT_EMAIL_SEARCH = os.getenv("MAPIT_EMAIL_SEARCH", '(UNSEEN FROM "mapit")')
COMMAND_EMAIL_SEARCH = os.getenv("COMMAND_EMAIL_SEARCH", "").strip()
if not COMMAND_EMAIL_SEARCH and GMAIL_ADDRESS:
    COMMAND_EMAIL_SEARCH = f'(UNSEEN FROM "{GMAIL_ADDRESS}" SUBJECT "mapit")'
elif not COMMAND_EMAIL_SEARCH:
    COMMAND_EMAIL_SEARCH = '(UNSEEN SUBJECT "mapit")'
MARK_EMAIL_AS_SEEN = os.getenv("MARK_EMAIL_AS_SEEN", "1") == "1"
ARCHIVE_EMAIL_AFTER_SUCCESS = os.getenv("ARCHIVE_EMAIL_AFTER_SUCCESS", "0") == "1"
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()
GITHUB_REPO = os.getenv("GITHUB_REPO", "").strip()
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main").strip() or "main"
GITHUB_DB_PATH = os.getenv("GITHUB_DB_PATH", "data/moto_maintenance.db").strip()
CHAIN_GREASE_INTERVAL_KM = float(os.getenv("CHAIN_GREASE_INTERVAL_KM", "1000"))
CHAIN_CLEAN_INTERVAL_KM = float(os.getenv("CHAIN_CLEAN_INTERVAL_KM", "3000"))
OIL_INTERVAL_KM = float(os.getenv("OIL_INTERVAL_KM", "12000"))
REPORT_REMINDER_DAYS = int(os.getenv("REPORT_REMINDER_DAYS", "7"))
REMINDER_COOLDOWN_DAYS = int(os.getenv("REMINDER_COOLDOWN_DAYS", "3"))
NTFY_TOPIC = os.getenv("NTFY_TOPIC", "").strip()
NTFY_TITLE = os.getenv("NTFY_TITLE", "Mapit mantenimiento")
NTFY_TAGS = os.getenv("NTFY_TAGS", "motorcycle,wrench")

@dataclass(frozen=True)
class Trip:
    trip_number: int; start_at: str; end_at: str; start_lon: float; start_lat: float; end_lon: float; end_lat: float; distance_km: float; duration_min: int; source_pdf: str
    @property
    def trip_key(self) -> str:
        raw = "|".join([self.start_at, self.end_at, f"{self.start_lon:.6f}", f"{self.start_lat:.6f}", f"{self.end_lon:.6f}", f"{self.end_lat:.6f}", f"{self.distance_km:.3f}"])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

def db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row; return con

def init_db() -> None:
    with db() as con:
        con.executescript('''
        CREATE TABLE IF NOT EXISTS trips (trip_key TEXT PRIMARY KEY, trip_number INTEGER, start_at TEXT NOT NULL, end_at TEXT NOT NULL, start_lon REAL, start_lat REAL, end_lon REAL, end_lat REAL, distance_km REAL NOT NULL, duration_min INTEGER, source_pdf TEXT, imported_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS maintenance_events (id INTEGER PRIMARY KEY AUTOINCREMENT, event_type TEXT NOT NULL, event_at TEXT NOT NULL, odometer_km REAL, trip_total_km REAL NOT NULL, note TEXT);
        CREATE TABLE IF NOT EXISTS processed_emails (message_uid TEXT PRIMARY KEY, subject TEXT, processed_at TEXT NOT NULL, pdf_name TEXT, inserted_trips INTEGER, added_km REAL);
        CREATE TABLE IF NOT EXISTS processed_commands (message_uid TEXT PRIMARY KEY, subject TEXT, command TEXT, processed_at TEXT NOT NULL, result TEXT);
        CREATE TABLE IF NOT EXISTS key_values (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS tyres (id INTEGER PRIMARY KEY AUTOINCREMENT, mounted_at TEXT NOT NULL, tyre_name TEXT, position TEXT, odometer_km REAL, trip_total_km REAL NOT NULL, note TEXT);
        ''')

def kv_get(key: str) -> Optional[str]:
    init_db();
    with db() as con:
        row = con.execute("SELECT value FROM key_values WHERE key=?", (key,)).fetchone(); return row["value"] if row else None

def kv_set(key: str, value: str) -> None:
    init_db();
    with db() as con: con.execute("INSERT OR REPLACE INTO key_values (key,value) VALUES (?,?)", (key,value))

def total_trip_km(con: sqlite3.Connection) -> float:
    return float(con.execute("SELECT COALESCE(SUM(distance_km),0) AS km FROM trips").fetchone()["km"])

def last_import_date() -> Optional[str]:
    init_db();
    with db() as con:
        row = con.execute("SELECT MAX(processed_at) AS d FROM processed_emails").fetchone(); return row["d"] if row and row["d"] else None

def last_trip_date() -> Optional[str]:
    init_db();
    with db() as con:
        row = con.execute("SELECT MAX(start_at) AS d FROM trips").fetchone(); return row["d"] if row and row["d"] else None

def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or " ").strip()

def parse_mapit_distance(km_str: str, m_str: str) -> float:
    return float(km_str.replace('.', '').replace(',', '.')) + float(m_str.replace('.', '').replace(',', '.')) / 1000.0

TRIP_RE = re.compile(r"Trayecto\s+(?P<num>\d+).*?Inicio estimado\s+(?P<start>\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}).*?Final\s+(?P<end>\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}).*?Posici[oó]n inicial\s+(?P<slon>-?\d+(?:\.\d+)?)\s+(?P<slat>-?\d+(?:\.\d+)?).*?Posici[oó]n final\s+(?P<elon>-?\d+(?:\.\d+)?)\s+(?P<elat>-?\d+(?:\.\d+)?).*?Distancia recorrida\s+(?P<km>\d+(?:[\.,]\d+)?)\s+km\s+(?P<m>\d+(?:[\.,]\d+)?)\s+m.*?Duraci[oó]n\s+(?:(?P<h>\d+)\s+h\s+)?(?P<min>\d+)\s+min", re.I | re.S)

def read_pdf_text(pdf_path: Path) -> str:
    try:
        result = subprocess.run(["pdftotext", str(pdf_path), "-"], check=True, capture_output=True, text=True, timeout=40)
        if result.stdout.strip(): return result.stdout
    except Exception: pass
    if pdfplumber is not None:
        with pdfplumber.open(str(pdf_path)) as pdf: return "\n".join(page.extract_text() or "" for page in pdf.pages)
    if PdfReader is None: raise SystemExit("Falta instalar pdfplumber/pypdf. Ejecuta: pip install -r requirements.txt")
    reader = PdfReader(str(pdf_path)); return "\n".join(page.extract_text() or "" for page in reader.pages)

def parse_pdf(pdf_path: Path) -> list[Trip]:
    norm = normalize_text(read_pdf_text(pdf_path)); trips=[]
    for m in TRIP_RE.finditer(norm):
        trips.append(Trip(int(m.group('num')), datetime.strptime(m.group('start'), '%d/%m/%Y %H:%M').isoformat(timespec='minutes'), datetime.strptime(m.group('end'), '%d/%m/%Y %H:%M').isoformat(timespec='minutes'), float(m.group('slon')), float(m.group('slat')), float(m.group('elon')), float(m.group('elat')), parse_mapit_distance(m.group('km'), m.group('m')), int(m.group('min')) + int(m.group('h') or 0)*60, pdf_path.name))
    return trips

def import_pdf(pdf_path: Path) -> tuple[int,int,float,float]:
    init_db(); trips=parse_pdf(pdf_path)
    if not trips: raise SystemExit(f"No he encontrado trayectos en: {pdf_path}")
    inserted=skipped=0; added_km=0.0; now=datetime.now().isoformat(timespec='seconds')
    with db() as con:
        for t in trips:
            try:
                con.execute("INSERT INTO trips (trip_key,trip_number,start_at,end_at,start_lon,start_lat,end_lon,end_lat,distance_km,duration_min,source_pdf,imported_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (t.trip_key,t.trip_number,t.start_at,t.end_at,t.start_lon,t.start_lat,t.end_lon,t.end_lat,t.distance_km,t.duration_min,t.source_pdf,now))
                inserted+=1; added_km += t.distance_km
            except sqlite3.IntegrityError: skipped += 1
        total=total_trip_km(con)
    return inserted, skipped, added_km, total

def get_last_event(con, event_type):
    return con.execute("SELECT * FROM maintenance_events WHERE event_type=? ORDER BY event_at DESC,id DESC LIMIT 1", (event_type,)).fetchone()

def km_since_event(con, event_type):
    last=get_last_event(con,event_type); current=total_trip_km(con); return current if not last else max(0.0, current-float(last['trip_total_km']))

def add_event(event_type: str, odometer_km: Optional[float]=None, note: str="") -> None:
    init_db();
    with db() as con: con.execute("INSERT INTO maintenance_events (event_type,event_at,odometer_km,trip_total_km,note) VALUES (?,?,?,?,?)", (event_type, datetime.now().isoformat(timespec='seconds'), odometer_km, total_trip_km(con), note))

def add_tyre_event(note: str="", odometer_km: Optional[float]=None) -> None:
    lines=[ln.strip() for ln in (note or '').splitlines() if ln.strip()]
    tyre_name=lines[0] if lines else 'Neumáticos'; position=lines[1] if len(lines)>1 else ''
    init_db();
    with db() as con:
        total=total_trip_km(con)
        con.execute("INSERT INTO tyres (mounted_at,tyre_name,position,odometer_km,trip_total_km,note) VALUES (?,?,?,?,?,?)", (datetime.now().isoformat(timespec='seconds'), tyre_name, position, odometer_km, total, note))
        con.execute("INSERT INTO maintenance_events (event_type,event_at,odometer_km,trip_total_km,note) VALUES (?,?,?,?,?)", ('neumaticos', datetime.now().isoformat(timespec='seconds'), odometer_km, total, note))

def parse_odometer_from_text(text: str) -> Optional[float]:
    m=re.search(r"(?i)(?:km|kms|kil[oó]metros|od[oó]metro|actuales)\D{0,20}(\d+(?:[.,]\d+)?)", text or '') or re.search(r"(?i)(\d+(?:[.,]\d+)?)\s*km", text or '')
    if not m: return None
    try: return float(m.group(1).replace('.','').replace(',','.'))
    except Exception: return None

def build_status_text() -> str:
    init_db()
    with db() as con:
        total_km=total_trip_km(con); trips_count=con.execute("SELECT COUNT(*) AS n FROM trips").fetchone()['n']
        chain_km=km_since_event(con,'engrase_cadena'); clean_km=km_since_event(con,'limpieza_cadena'); oil_km=km_since_event(con,'aceite')
        last_chain=get_last_event(con,'engrase_cadena'); last_clean=get_last_event(con,'limpieza_cadena'); last_oil=get_last_event(con,'aceite')
        last_tyre=con.execute("SELECT * FROM tyres ORDER BY mounted_at DESC,id DESC LIMIT 1").fetchone()
    def counter(name, km_done, interval):
        left=interval-km_done
        if left <= 0: return f"⚠️ {name}: {km_done:.0f}/{interval:.0f} km — TOCA ya"
        if left <= interval*0.15: return f"🔶 {name}: {km_done:.0f}/{interval:.0f} km — quedan {left:.0f} km"
        return f"✅ {name}: {km_done:.0f}/{interval:.0f} km — quedan {left:.0f} km"
    lines=["🏍️ Estado mantenimiento Mapit", f"Trayectos guardados: {trips_count}", f"Km totales importados: {total_km:.3f} km", "", counter('Engrase cadena', chain_km, CHAIN_GREASE_INTERVAL_KM), counter('Limpieza cadena', clean_km, CHAIN_CLEAN_INTERVAL_KM), counter('Aceite/revisión', oil_km, OIL_INTERVAL_KM), "", f"Último engrase: {last_chain['event_at'] if last_chain else 'sin registrar'}", f"Última limpieza: {last_clean['event_at'] if last_clean else 'sin registrar'}", f"Última revisión aceite: {last_oil['event_at'] if last_oil else 'sin registrar'}"]
    if last_tyre:
        lines += [f"Últimos neumáticos: {last_tyre['tyre_name']} {last_tyre['position'] or ''}".strip(), f"Km con neumáticos actuales: {total_km-float(last_tyre['trip_total_km']):.0f} km"]
    return "\n".join(lines)

def commands_help_text() -> str:
    return "\n".join(["📌 Comandos por email", "Envíate un correo a ti mismo con el asunto:", "• mapit engrase", "• mapit limpieza", "• mapit aceite", "• mapit revision", "• mapit estado", "• mapit neumaticos", "• mapit itv", "", "El cuerpo del correo se guarda como nota."])

def report_reminder_text(force=False) -> str:
    li=last_import_date(); lt=last_trip_date()
    if not li: return "⏰ Recuerda generar un informe de Mapit para empezar a alimentar el contador."
    try: days=(datetime.now()-datetime.fromisoformat(li)).days
    except Exception: return "⏰ Recuerda generar un nuevo informe de Mapit cuando termines tus próximas rutas."
    if force or days >= REPORT_REMINDER_DAYS: return f"⏰ Hace {days} días que no importo un informe nuevo. Genera un informe de Mapit para mantener los km actualizados."
    return f"⏰ Último trayecto importado: {lt}. Recuerda generar informe tras nuevas rutas." if lt else "⏰ Recuerda generar informe de Mapit tras tus rutas."

def should_send_report_reminder() -> bool:
    li=last_import_date()
    if not li: return True
    try:
        if (datetime.now()-datetime.fromisoformat(li)).days < REPORT_REMINDER_DAYS: return False
    except Exception: return False
    ls=kv_get('last_report_reminder_sent')
    if ls:
        try:
            if (datetime.now()-datetime.fromisoformat(ls)).days < REMINDER_COOLDOWN_DAYS: return False
        except Exception: pass
    return True

def build_footer() -> str:
    return "\n\n" + "\n".join(["──────────────", report_reminder_text(), "", commands_help_text()])

def send_ntfy(message: str, title: str=NTFY_TITLE, priority: str='default') -> None:
    if not NTFY_TOPIC: return
    if requests is None: print('Falta requests para ntfy'); return
    topic = NTFY_TOPIC if NTFY_TOPIC.startswith('http') else f"https://ntfy.sh/{NTFY_TOPIC}"
    safe_title=(title or 'Mapit mantenimiento').encode('ascii', errors='ignore').decode('ascii').strip() or 'Mapit mantenimiento'
    try: requests.post(topic, data=message.encode('utf-8'), headers={'Title': safe_title, 'Priority': priority, 'Tags': NTFY_TAGS}, timeout=20).raise_for_status()
    except Exception as exc: print(f"No he podido enviar ntfy: {exc}")

def github_headers(): return {'Authorization': f'Bearer {GITHUB_TOKEN}', 'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'}
def github_enabled(): return bool(GITHUB_TOKEN and GITHUB_REPO and requests is not None)
def github_download_db():
    if not github_enabled(): return
    url=f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_DB_PATH}"; r=requests.get(url, headers=github_headers(), params={'ref': GITHUB_BRANCH}, timeout=30)
    if r.status_code==404: return
    r.raise_for_status(); DB_PATH.parent.mkdir(parents=True, exist_ok=True); DB_PATH.write_bytes(base64.b64decode(r.json()['content']))
def github_upload_db(commit_message='Actualiza DB mantenimiento Mapit'):
    if not github_enabled() or not DB_PATH.exists(): return
    url=f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_DB_PATH}"; get=requests.get(url, headers=github_headers(), params={'ref': GITHUB_BRANCH}, timeout=30)
    sha=get.json().get('sha') if get.status_code==200 else None
    if get.status_code not in (200,404): get.raise_for_status()
    payload={'message': commit_message, 'content': base64.b64encode(DB_PATH.read_bytes()).decode('ascii'), 'branch': GITHUB_BRANCH}
    if sha: payload['sha']=sha
    r=requests.put(url, headers=github_headers(), json=payload, timeout=30); r.raise_for_status()

def extract_text_and_html(msg: Message) -> str:
    parts=[]
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() in ('text/plain','text/html'):
                payload=part.get_payload(decode=True)
                if payload: parts.append(payload.decode(part.get_content_charset() or 'utf-8', errors='replace'))
    else:
        payload=msg.get_payload(decode=True)
        if payload: parts.append(payload.decode(msg.get_content_charset() or 'utf-8', errors='replace'))
    return "\n".join(parts)

def find_mapit_report_links(text):
    candidates=re.findall(r'https?://[^\s"\'<>]+', unescape(text)); links=[]
    for raw in candidates:
        url=raw.rstrip(').,;]'); decoded=unquote(url)
        if 'route-report' in decoded or 'InformeMapit' in decoded or 'pdfUrl=' in decoded: links.append(url)
    seen=set(); out=[]
    for l in links:
        if l not in seen: seen.add(l); out.append(l)
    return out

def filename_from_url(url, fallback='InformeMapit.pdf'):
    decoded=unquote(url); m=re.search(r'(InformeMapit_[^/?#]+\.pdf)', decoded)
    if m: return re.sub(r'[^A-Za-z0-9_.() -]+','_',m.group(1))
    name=Path(urlparse(decoded).path).name
    return re.sub(r'[^A-Za-z0-9_.() -]+','_',name) if name.lower().endswith('.pdf') else fallback

def download_pdf_from_link(link, dest_dir):
    if requests is None: raise SystemExit('Falta requests. Ejecuta: pip install -r requirements.txt')
    dest_dir.mkdir(parents=True, exist_ok=True); dest=dest_dir/filename_from_url(link)
    r=requests.get(link, headers={'User-Agent':'Mozilla/5.0 mapit-maintenance/3.0'}, allow_redirects=True, timeout=60); r.raise_for_status()
    ct=r.headers.get('content-type','').lower()
    if 'pdf' not in ct and not r.content.startswith(b'%PDF'):
        links=find_mapit_report_links(r.text)
        if links and links[0] != link: return download_pdf_from_link(links[0], dest_dir)
        raise RuntimeError(f'El enlace no devolvió un PDF. Content-Type={ct}')
    dest.write_bytes(r.content); return dest

def imap_connect():
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD: raise SystemExit('Faltan GMAIL_ADDRESS y/o GMAIL_APP_PASSWORD.')
    imap=imaplib.IMAP4_SSL('imap.gmail.com'); imap.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD); imap.select(GMAIL_MAILBOX); return imap

def imap_search(imap, query, max_emails):
    status,data=imap.uid('search', None, query)
    if status!='OK': raise RuntimeError(f'Gmail search falló: {status} {data}')
    return [x.decode('ascii') for x in data[0].split()[-max_emails:]]
def fetch_email(imap, uid):
    status,msg_data=imap.uid('fetch', uid, '(RFC822)')
    if status!='OK' or not msg_data or not msg_data[0]: return None
    return email.message_from_bytes(msg_data[0][1])
def email_subject(msg): return str(email.header.make_header(email.header.decode_header(msg.get('Subject',''))))
def mark_seen(imap, uid):
    if MARK_EMAIL_AS_SEEN: imap.uid('store', uid, '+FLAGS', '(\\Seen)')
def archive_email(imap, uid):
    if ARCHIVE_EMAIL_AFTER_SUCCESS:
        imap.uid('store', uid, '+FLAGS', '(\\Seen)'); imap.uid('store', uid, '+X-GM-LABELS', '(MapitProcesado)'); imap.uid('store', uid, '+FLAGS', '(\\Deleted)')

def already_processed_report(uid):
    init_db();
    with db() as con: return con.execute('SELECT 1 FROM processed_emails WHERE message_uid=?',(uid,)).fetchone() is not None
def mark_processed_report(uid, subject, pdf_name, inserted, added):
    init_db();
    with db() as con: con.execute('INSERT OR REPLACE INTO processed_emails (message_uid,subject,processed_at,pdf_name,inserted_trips,added_km) VALUES (?,?,?,?,?,?)', (uid,subject,datetime.now().isoformat(timespec='seconds'),pdf_name,inserted,added))
def already_processed_command(uid):
    init_db();
    with db() as con: return con.execute('SELECT 1 FROM processed_commands WHERE message_uid=?',(uid,)).fetchone() is not None
def mark_processed_command(uid, subject, command, result):
    init_db();
    with db() as con: con.execute('INSERT OR REPLACE INTO processed_commands (message_uid,subject,command,processed_at,result) VALUES (?,?,?,?,?)', (uid,subject,command,datetime.now().isoformat(timespec='seconds'),result))

def command_from_subject(subject):
    s=normalize_text(subject).lower(); s=re.sub(r'^(re|fw|fwd)\s*:\s*','',s)
    if not s.startswith('mapit'): return None
    s=s.replace('á','a').replace('é','e').replace('í','i').replace('ó','o').replace('ú','u').replace('ü','u')
    for word, cmd in [('engrase','engrase_cadena'),('grasa','engrase_cadena'),('cadena','engrase_cadena'),('limpieza','limpieza_cadena'),('limpiar','limpieza_cadena'),('aceite','aceite'),('revision','revision'),('estado','estado'),('neumaticos','neumaticos'),('ruedas','neumaticos'),('itv','itv')]:
        if word in s: return cmd
    return None

def execute_command(command, body=''):
    note=normalize_text(re.sub(r'<[^>]+>',' ',body or ''))[:1000]; odometer=parse_odometer_from_text(note)
    if command=='engrase_cadena': add_event('engrase_cadena', odometer, note or 'Engrase registrado por email'); return '✅ Engrase de cadena registrado. El contador de engrase se reinicia desde este punto.'
    if command=='limpieza_cadena': add_event('limpieza_cadena', odometer, note or 'Limpieza de cadena registrada por email'); return '✅ Limpieza de cadena registrada. El contador de limpieza se reinicia desde este punto.'
    if command=='aceite': add_event('aceite', odometer, note or 'Aceite registrado por email'); return '✅ Cambio/revisión de aceite registrado. El contador de aceite se reinicia desde este punto.'
    if command=='revision': add_event('revision', odometer, note or 'Revisión registrada por email'); return '✅ Revisión general registrada. Los contadores específicos no se reinician salvo que indiques aceite/engrase/limpieza.'
    if command=='neumaticos': add_tyre_event(note, odometer); return '✅ Neumáticos registrados. Empiezo a contar km desde este punto para el último juego registrado.'
    if command=='itv': add_event('itv', odometer, note or 'ITV registrada por email'); return '✅ ITV registrada en el historial. No modifica contadores de cadena/aceite.'
    if command=='estado': return '📊 Estado solicitado por email. No he modificado ningún contador.'
    return '⚠️ Comando no reconocido.'

def process_command_emails(imap, max_emails=10):
    processed=0; results=[]
    for uid in imap_search(imap, COMMAND_EMAIL_SEARCH, max_emails):
        if already_processed_command(uid): continue
        msg=fetch_email(imap, uid)
        if msg is None: continue
        subject=email_subject(msg); command=command_from_subject(subject)
        if not command: continue
        result=execute_command(command, extract_text_and_html(msg)); mark_processed_command(uid, subject, command, result); mark_seen(imap, uid); archive_email(imap, uid)
        processed += 1; results.append(f'📧 {subject}\n{result}')
    return processed, results

def process_report_emails(imap, max_emails=10):
    total_inserted=total_skipped=0; total_added=0.0; processed_count=0; messages=[]
    for uid in imap_search(imap, MAPIT_EMAIL_SEARCH, max_emails):
        if already_processed_report(uid): continue
        msg=fetch_email(imap, uid)
        if msg is None: continue
        subject=email_subject(msg); links=find_mapit_report_links(extract_text_and_html(msg))
        if not links: continue
        pdf_path=download_pdf_from_link(links[0], DOWNLOAD_DIR); inserted, skipped, added, total=import_pdf(pdf_path); mark_processed_report(uid, subject, pdf_path.name, inserted, added)
        total_inserted += inserted; total_skipped += skipped; total_added += added; processed_count += 1
        messages.append(f'- {pdf_path.name}: +{inserted} trayectos, {added:.3f} km'); mark_seen(imap, uid); archive_email(imap, uid)
    return processed_count, total_inserted, total_skipped, total_added, messages

def run_gmail(send_notification=False, max_emails=10):
    github_download_db(); init_db(); imap=imap_connect()
    try:
        cmd_count, cmd_results=process_command_emails(imap, max_emails); rep_count, inserted, skipped, added, report_messages=process_report_emails(imap, max_emails)
        if ARCHIVE_EMAIL_AFTER_SUCCESS: imap.expunge()
    finally: imap.logout()
    if cmd_count or rep_count: github_upload_db('Actualiza mantenimiento Mapit V3')
    with db() as con: current_total=total_trip_km(con)
    parts=[]
    if rep_count: parts += ['🏍️ Mapit Gmail procesado', f'Informes procesados: {rep_count}', f'Nuevos trayectos: {inserted}', f'Duplicados ignorados: {skipped}', f'Km añadidos: {added:.3f}', f'Km totales: {current_total:.3f}', '', *report_messages]
    if cmd_count:
        if parts: parts += ['', '──────────────']
        parts += ['📧 Comandos procesados', f'Comandos: {cmd_count}', '', *cmd_results]
    if not parts: parts=['🏍️ Mapit Gmail','No hay informes ni comandos nuevos para procesar.']
    reminder_sent_now=False
    if not rep_count and not cmd_count and should_send_report_reminder():
        parts += ['', '──────────────', report_reminder_text(force=True)]; kv_set('last_report_reminder_sent', datetime.now().isoformat(timespec='seconds')); github_upload_db('Actualiza recordatorio Mapit'); reminder_sent_now=True
    msg='\n'.join(parts + ['', build_status_text(), build_footer()])
    if send_notification and (rep_count or cmd_count or reminder_sent_now or os.getenv('NTFY_NOTIFY_EMPTY','0')=='1'):
        send_ntfy(msg, priority='high' if 'TOCA ya' in msg else 'default')
    return msg

def main():
    parser=argparse.ArgumentParser(description='Importa informes Mapit y controla mantenimiento de la moto.'); sub=parser.add_subparsers(dest='cmd', required=True)
    p_imp=sub.add_parser('importar'); p_imp.add_argument('pdf', type=Path); p_imp.add_argument('--ntfy', action='store_true')
    p_gmail=sub.add_parser('importar-gmail'); p_gmail.add_argument('--ntfy', action='store_true'); p_gmail.add_argument('--max-emails', type=int, default=10)
    sub.add_parser('estado')
    p_eng=sub.add_parser('engrase'); p_eng.add_argument('--km-actuales', type=float, default=None); p_eng.add_argument('--nota', default='')
    p_clean=sub.add_parser('limpieza-cadena'); p_clean.add_argument('--km-actuales', type=float, default=None); p_clean.add_argument('--nota', default='')
    p_oil=sub.add_parser('revision'); p_oil.add_argument('--tipo', default='aceite'); p_oil.add_argument('--km-actuales', type=float, default=None); p_oil.add_argument('--nota', default='')
    args=parser.parse_args()
    if args.cmd=='importar':
        github_download_db(); inserted, skipped, added, total=import_pdf(args.pdf); github_upload_db('Importa PDF Mapit')
        msg='\n'.join(['🏍️ Mapit procesado', f'PDF: {args.pdf.name}', f'Nuevos trayectos: {inserted}', f'Duplicados ignorados: {skipped}', f'Km añadidos: {added:.3f}', f'Km totales: {total:.3f}', '', build_status_text(), build_footer()]); print(msg)
        if args.ntfy: send_ntfy(msg, priority='high' if 'TOCA ya' in msg else 'default')
    elif args.cmd=='importar-gmail': print(run_gmail(send_notification=args.ntfy, max_emails=args.max_emails))
    elif args.cmd=='estado': github_download_db(); print(build_status_text()+build_footer())
    elif args.cmd=='engrase': github_download_db(); add_event('engrase_cadena', args.km_actuales, args.nota); github_upload_db('Registra engrase cadena'); print('✅ Engrase registrado.\n'+build_status_text())
    elif args.cmd=='limpieza-cadena': github_download_db(); add_event('limpieza_cadena', args.km_actuales, args.nota); github_upload_db('Registra limpieza cadena'); print('✅ Limpieza registrada.\n'+build_status_text())
    elif args.cmd=='revision': github_download_db(); event_type='aceite' if args.tipo.lower().strip() in ('aceite','oil') else 'revision'; add_event(event_type, args.km_actuales, f'{args.tipo}. {args.nota}'.strip()); github_upload_db('Registra revision moto'); print('✅ Revisión registrada.\n'+build_status_text())
if __name__ == '__main__': main()
