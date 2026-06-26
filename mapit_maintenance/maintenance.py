from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from .config import CHAIN_CLEAN_INTERVAL_KM, CHAIN_GREASE_INTERVAL_KM, OIL_INTERVAL_KM
from .database import db, init_db
from .pdf_parser import parse_pdf


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
    return con.execute(
        "SELECT * FROM maintenance_events WHERE event_type = ? ORDER BY event_at DESC, id DESC LIMIT 1",
        (event_type,),
    ).fetchone()


def km_since_event(con: sqlite3.Connection, event_type: str) -> float:
    last = get_last_event(con, event_type)
    current = total_trip_km(con)
    return current if not last else max(0.0, current - float(last["trip_total_km"]))


def add_event(event_type: str, odometer_km: Optional[float] = None, note: str = "") -> None:
    init_db()
    with db() as con:
        con.execute(
            "INSERT INTO maintenance_events (event_type, event_at, odometer_km, trip_total_km, note) VALUES (?, ?, ?, ?, ?)",
            (event_type, datetime.now().isoformat(timespec="seconds"), odometer_km, total_trip_km(con), note.strip()),
        )


def counter_line(name: str, km_done: float, interval: float) -> str:
    left = interval - km_done
    if left <= 0:
        return f"⚠️ {name}: {km_done:.0f}/{interval:.0f} km — TOCA ya"
    if left <= interval * 0.15:
        return f"🔶 {name}: {km_done:.0f}/{interval:.0f} km — quedan {left:.0f} km"
    return f"✅ {name}: {km_done:.0f}/{interval:.0f} km — quedan {left:.0f} km"


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
    return "\n".join([
        "🏍️ Estado mantenimiento Mapit",
        f"Trayectos guardados: {trips_count}",
        f"Km totales importados: {total_km:.3f} km",
        "",
        counter_line("Engrase cadena", chain_km, CHAIN_GREASE_INTERVAL_KM),
        counter_line("Limpieza cadena", clean_km, CHAIN_CLEAN_INTERVAL_KM),
        counter_line("Aceite/revisión", oil_km, OIL_INTERVAL_KM),
        "",
        f"Último engrase: {last_chain['event_at'] if last_chain else 'sin registrar'}",
        f"Última limpieza: {last_clean['event_at'] if last_clean else 'sin registrar'}",
        f"Última revisión aceite: {last_oil['event_at'] if last_oil else 'sin registrar'}",
    ])


def build_history_text(limit: int = 12) -> str:
    init_db()
    with db() as con:
        rows = con.execute(
            "SELECT event_type, event_at, trip_total_km, note FROM maintenance_events ORDER BY event_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    if not rows:
        return "📜 Historial Mapit\nSin mantenimientos registrados todavía."
    names = {
        "engrase_cadena": "Engrase cadena",
        "limpieza_cadena": "Limpieza cadena",
        "aceite": "Aceite/revisión",
        "revision": "Revisión",
        "itv": "ITV",
        "neumaticos": "Neumáticos",
        "repostaje": "Repostaje",
        "presiones": "Presiones",
        "seguro": "Seguro",
        "bateria": "Batería",
    }
    lines = ["📜 Historial Mapit"]
    for r in rows:
        date = r["event_at"][:10]
        label = names.get(r["event_type"], r["event_type"])
        note = f" — {r['note']}" if r["note"] else ""
        lines.append(f"{date}: {label} ({r['trip_total_km']:.0f} km){note}")
    return "\n".join(lines)


def build_month_stats_text() -> str:
    init_db()
    since = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")
    with db() as con:
        row = con.execute(
            """
            SELECT COUNT(*) AS n, COALESCE(SUM(distance_km),0) AS km, COALESCE(SUM(duration_min),0) AS minutes,
                   COALESCE(MAX(distance_km),0) AS max_km
            FROM trips WHERE start_at >= ?
            """,
            (since[:16],),
        ).fetchone()
    n = int(row["n"])
    km = float(row["km"])
    avg = km / n if n else 0.0
    hours = float(row["minutes"]) / 60.0
    return "\n".join([
        "📊 Estadísticas del mes",
        f"Trayectos: {n}",
        f"Km: {km:.1f}",
        f"Media/trayecto: {avg:.1f} km",
        f"Trayecto más largo: {float(row['max_km']):.1f} km",
        f"Tiempo en moto: {hours:.1f} h",
    ])


def last_trip_import_date() -> Optional[datetime]:
    init_db()
    with db() as con:
        row = con.execute("SELECT MAX(imported_at) AS last FROM trips").fetchone()
    if not row or not row["last"]:
        return None
    try:
        return datetime.fromisoformat(row["last"])
    except ValueError:
        return None
