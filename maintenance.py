from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import CHAIN_CLEAN_INTERVAL_KM, CHAIN_GREASE_INTERVAL_KM, REVISION_INTERVAL_KM, WHEELS_INTERVAL_KM
from .database import db, get_setting, init_db, set_setting
from .pdf_parser import parse_pdf


def total_trip_km(con: sqlite3.Connection) -> float:
    return float(con.execute("SELECT COALESCE(SUM(distance_km), 0) AS km FROM trips").fetchone()["km"])


def _float_setting(key: str, default: float | None = None) -> float | None:
    try:
        value = get_setting(key)
        if value in (None, ""):
            return default
        return float(value)
    except Exception:
        return default


def get_km_offset() -> float:
    # Compatibilidad con el sistema antiguo. Si existe base de odómetro real,
    # el offset fijo deja de usarse.
    value = _float_setting("km_offset", 0.0)
    return float(value or 0.0)


def get_odometer_base() -> tuple[float | None, float | None]:
    real_base = _float_setting("odometer_real_base", None)
    mapit_base = _float_setting("odometer_mapit_base", None)
    if real_base is None or mapit_base is None:
        return None, None
    return real_base, mapit_base


def adjusted_total_km(con: sqlite3.Connection) -> float:
    raw_km = total_trip_km(con)
    real_base, mapit_base = get_odometer_base()
    if real_base is not None and mapit_base is not None:
        return real_base + max(0.0, raw_km - mapit_base)
    return raw_km + get_km_offset()


def set_km_offset(offset: float) -> None:
    # Compatibilidad con comando antiguo "mapit ajuste".
    # Si ya hay base de odómetro, aplica el ajuste sobre el odómetro real base.
    real_base, mapit_base = get_odometer_base()
    if real_base is not None and mapit_base is not None:
        set_setting("odometer_real_base", f"{real_base + offset:.3f}")
        return
    set_setting("km_offset", f"{offset:.3f}")


def add_km_offset(delta: float) -> float:
    real_base, mapit_base = get_odometer_base()
    if real_base is not None and mapit_base is not None:
        new_real_base = real_base + delta
        set_setting("odometer_real_base", f"{new_real_base:.3f}")
        return new_real_base - mapit_base
    new_offset = get_km_offset() + delta
    set_setting("km_offset", f"{new_offset:.3f}")
    return new_offset


def set_real_odometer_km(real_km: float) -> float:
    """Fija el odómetro real actual y crea una base contra los km Mapit actuales.

    A partir de aquí:
      km reales estimados = odómetro real base + (km Mapit actual - km Mapit base)

    Así, si luego importas informes atrasados o nuevos, los km se suman desde
    esta base y no dependes de un offset fijo.
    """
    init_db()
    with db() as con:
        raw_km = total_trip_km(con)
    set_setting("odometer_real_base", f"{real_km:.3f}")
    set_setting("odometer_mapit_base", f"{raw_km:.3f}")
    # Mantener km_offset actualizado solo como dato informativo/compatibilidad.
    offset = real_km - raw_km
    set_setting("km_offset", f"{offset:.3f}")
    return offset


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
        total = adjusted_total_km(con)
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


def set_counter(event_type: str, km_done: float, note: str = "") -> None:
    init_db()
    km_done = max(0.0, float(km_done))
    with db() as con:
        current_raw = total_trip_km(con)
        baseline = max(0.0, current_raw - km_done)
        con.execute(
            "INSERT INTO maintenance_events (event_type, event_at, odometer_km, trip_total_km, note) VALUES (?, ?, ?, ?, ?)",
            (
                event_type,
                datetime.now().isoformat(timespec="seconds"),
                None,
                baseline,
                (note or f"Contador fijado manualmente a {km_done:.0f} km").strip(),
            ),
        )


def counter_line(name: str, km_done: float, interval: float) -> str:
    left = interval - km_done
    if left <= 0:
        return f"⚠️ {name}: {km_done:.0f}/{interval:.0f} km — TOCA ya"
    if left <= interval * 0.15:
        return f"🔶 {name}: {km_done:.0f}/{interval:.0f} km — quedan {left:.0f} km"
    return f"✅ {name}: {km_done:.0f}/{interval:.0f} km — quedan {left:.0f} km"


def bar_line(icon: str, name: str, counter: dict) -> str:
    interval = max(1.0, float(counter["interval_km"]))
    km_done = max(0.0, float(counter["km"]))
    ratio = min(1.0, km_done / interval)
    filled = int(round(ratio * 10))
    bar = "█" * filled + "░" * (10 - filled)
    if counter["due"]:
        suffix = "TOCA"
    else:
        suffix = f"quedan {counter['remaining_km']:.0f} km"
    return f"{icon} {name:<9} {bar} {suffix}"


def build_status_text() -> str:
    init_db()
    with db() as con:
        raw_total_km = total_trip_km(con)
        total_km = adjusted_total_km(con)
        real_base, mapit_base = get_odometer_base()
        offset = total_km - raw_total_km
        trips_count = con.execute("SELECT COUNT(*) AS n FROM trips").fetchone()["n"]
        chain_km = km_since_event(con, "engrase_cadena")
        clean_km = km_since_event(con, "limpieza_cadena")
        wheels_km = km_since_event(con, "ruedas")
        revision_km = km_since_event(con, "aceite")
        last_chain = get_last_event(con, "engrase_cadena")
        last_clean = get_last_event(con, "limpieza_cadena")
        last_wheels = get_last_event(con, "ruedas")
        last_revision = get_last_event(con, "aceite")
    lines = [
        "🏍️ Estado mantenimiento Mapit",
        f"Trayectos guardados: {trips_count}",
        f"Km reales estimados: {total_km:.3f} km",
    ]
    if real_base is not None and mapit_base is not None:
        lines.append(f"Odómetro base: {real_base:.3f} km · Mapit base: {mapit_base:.3f} km")
        lines.append(f"Km Mapit: {raw_total_km:.3f} km · Nuevos desde base: {max(0.0, raw_total_km - mapit_base):.3f} km")
    elif abs(offset) >= 0.001:
        lines.append(f"Km Mapit: {raw_total_km:.3f} km · Ajuste: {offset:+.3f} km")
    lines.extend([
        "",
        counter_line("Engrase cadena", chain_km, CHAIN_GREASE_INTERVAL_KM),
        counter_line("Limpieza cadena", clean_km, CHAIN_CLEAN_INTERVAL_KM),
        counter_line("Ruedas", wheels_km, WHEELS_INTERVAL_KM),
        counter_line("Revisión/mantenimiento", revision_km, REVISION_INTERVAL_KM),
        "",
        f"Último engrase: {last_chain['event_at'] if last_chain else 'sin registrar'}",
        f"Última limpieza: {last_clean['event_at'] if last_clean else 'sin registrar'}",
        f"Últimas ruedas: {last_wheels['event_at'] if last_wheels else 'sin registrar'}",
        f"Última revisión: {last_revision['event_at'] if last_revision else 'sin registrar'}",
    ])
    return "\n".join(lines)


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
        "ruedas": "Ruedas",
        "neumaticos": "Neumáticos",
        "aceite": "Revisión/mantenimiento",
        "revision": "Revisión",
        "itv": "ITV",
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


def counter_payload(km_done: float, interval: float) -> dict:
    remaining = interval - km_done
    due = remaining <= 0
    soon = (not due) and remaining <= interval * 0.15
    return {
        "km": round(km_done, 3),
        "interval_km": round(interval, 3),
        "remaining_km": round(max(0.0, remaining), 3),
        "due": due,
        "soon": soon,
        "level": "due" if due else "soon" if soon else "ok",
    }


def build_status_payload() -> dict:
    init_db()
    with db() as con:
        raw_total_km = total_trip_km(con)
        total_km = adjusted_total_km(con)
        real_base, mapit_base = get_odometer_base()
        offset = total_km - raw_total_km
        trips_count = int(con.execute("SELECT COUNT(*) AS n FROM trips").fetchone()["n"])
        chain_km = km_since_event(con, "engrase_cadena")
        clean_km = km_since_event(con, "limpieza_cadena")
        wheels_km = km_since_event(con, "ruedas")
        revision_km = km_since_event(con, "aceite")
        last_chain = get_last_event(con, "engrase_cadena")
        last_clean = get_last_event(con, "limpieza_cadena")
        last_wheels = get_last_event(con, "ruedas")
        last_revision = get_last_event(con, "aceite")

    chain = counter_payload(chain_km, CHAIN_GREASE_INTERVAL_KM)
    clean = counter_payload(clean_km, CHAIN_CLEAN_INTERVAL_KM)
    wheels = counter_payload(wheels_km, WHEELS_INTERVAL_KM)
    revision = counter_payload(revision_km, REVISION_INTERVAL_KM)
    levels = [chain["level"], clean["level"], wheels["level"], revision["level"]]
    alert_level = "due" if "due" in levels else "soon" if "soon" in levels else "ok"

    last_report = last_trip_import_date()
    last_report_days = None
    if last_report:
        last_report_days = (datetime.now() - last_report).days

    if chain["due"]:
        text_short = f"Cadena {chain_km:.0f}/{CHAIN_GREASE_INTERVAL_KM:.0f} km · TOCA engrase"
    else:
        text_short = f"Cadena {chain_km:.0f}/{CHAIN_GREASE_INTERVAL_KM:.0f} km · quedan {chain['remaining_km']:.0f} km"

    text_block_lines = [
        "🏍️ Mantenimiento",
        f"Km reales estimados: {total_km:.1f} km",
    ]
    if real_base is not None and mapit_base is not None:
        text_block_lines.append(f"Base real: {real_base:.1f} km · Mapit base: {mapit_base:.1f} km")
        text_block_lines.append(f"Km Mapit actuales: {raw_total_km:.1f} km")
    elif abs(offset) >= 0.001:
        text_block_lines.append(f"Km Mapit: {raw_total_km:.1f} km · ajuste {offset:+.1f} km")
    text_block_lines.extend([
        bar_line("⛓️", "Cadena", chain),
        bar_line("🧽", "Limpieza", clean),
        bar_line("🛞", "Ruedas", wheels),
        bar_line("🔧", "Revisión", revision),
    ])
    if last_report_days is not None:
        text_block_lines.append(f"Último informe Mapit: hace {last_report_days} días")

    if alert_level == "due":
        text_block_lines.append("📧 Si ya lo hiciste: mapit engrase / mapit limpieza / mapit ruedas / mapit revision")
    elif alert_level == "soon":
        text_block_lines.append("📧 Comandos: mapit actualizar · mapit estado")

    return {
        "ok": True,
        "source": "mapit_mantenimiento",
        "km_totales": round(total_km, 3),
        "km_reales_estimados": round(total_km, 3),
        "km_mapit": round(raw_total_km, 3),
        "km_ajuste": round(offset, 3),
        "odometer_mode": "base" if real_base is not None and mapit_base is not None else "offset",
        "odometer_real_base": round(real_base, 3) if real_base is not None else None,
        "odometer_mapit_base": round(mapit_base, 3) if mapit_base is not None else None,
        "mapit_km_since_base": round(max(0.0, raw_total_km - mapit_base), 3) if mapit_base is not None else None,
        "trayectos_guardados": trips_count,
        "alert_level": alert_level,
        "should_show": alert_level != "ok",
        "cadena": chain,
        "limpieza": clean,
        "ruedas": wheels,
        "revision": revision,
        "aceite": revision,
        "last_report_days": last_report_days,
        "ultimo_engrase": last_chain["event_at"] if last_chain else None,
        "ultima_limpieza": last_clean["event_at"] if last_clean else None,
        "ultimas_ruedas": last_wheels["event_at"] if last_wheels else None,
        "ultima_revision": last_revision["event_at"] if last_revision else None,
        "ultima_revision_aceite": last_revision["event_at"] if last_revision else None,
        "texto_corto": text_short,
        "texto_bloque": "\n".join(text_block_lines),
        "comandos_rapidos": [
            "mapit estado",
            "mapit actualizar",
            "mapit engrase",
            "mapit limpieza",
            "mapit ruedas",
            "mapit revision",
            "mapit km 15573",
            "mapit ayuda",
        ],
    }
