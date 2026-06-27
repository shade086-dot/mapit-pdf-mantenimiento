from __future__ import annotations

from datetime import datetime, timedelta

from .config import CHAIN_CLEAN_INTERVAL_KM, CHAIN_GREASE_INTERVAL_KM, OIL_INTERVAL_KM, REPORT_REMINDER_DAYS, REMINDER_COOLDOWN_HOURS
from .database import db, get_setting, init_db, set_setting
from .maintenance import km_since_event, last_trip_import_date


def quick_help_text() -> str:
    return "\n".join([
        "📌 Acciones rápidas por email",
        "Asunto: mapit estado",
        "Asunto: mapit engrase",
        "Asunto: mapit limpieza",
        "Asunto: mapit aceite",
        "Asunto: mapit revision",
        "Asunto: mapit neumaticos",
        "Asunto: mapit historial",
        "Asunto: mapit stats",
        "",
        "📄 Recuerda generar el informe de Mapit cuando quieras actualizar kilómetros.",
    ])


def smart_reminders_text() -> str:
    init_db()
    notes: list[str] = []
    with db() as con:
        chain = km_since_event(con, "engrase_cadena")
        clean = km_since_event(con, "limpieza_cadena")
        oil = km_since_event(con, "aceite")
    if chain >= CHAIN_GREASE_INTERVAL_KM:
        notes.append("⚠️ Cadena: toca engrase ya.")
    elif CHAIN_GREASE_INTERVAL_KM - chain <= 150:
        notes.append(f"🔶 Cadena: quedan unos {CHAIN_GREASE_INTERVAL_KM - chain:.0f} km para engrase.")
    if clean >= CHAIN_CLEAN_INTERVAL_KM:
        notes.append("⚠️ Limpieza cadena: toca limpieza ya.")
    elif CHAIN_CLEAN_INTERVAL_KM - clean <= 300:
        notes.append(f"🔶 Limpieza cadena: quedan unos {CHAIN_CLEAN_INTERVAL_KM - clean:.0f} km.")
    if oil >= OIL_INTERVAL_KM:
        notes.append("⚠️ Aceite/revisión: toca revisión ya.")
    elif OIL_INTERVAL_KM - oil <= 1000:
        notes.append(f"🔶 Aceite/revisión: quedan unos {OIL_INTERVAL_KM - oil:.0f} km.")

    last = last_trip_import_date()
    if last is None:
        notes.append("📄 Aún no hay informes Mapit importados. Genera uno desde la app.")
    else:
        days = (datetime.now() - last).days
        if days >= REPORT_REMINDER_DAYS:
            notes.append(f"📄 Hace {days} días que no entra un informe nuevo de Mapit.")
    if not notes:
        return ""
    return "\n".join(["🧠 Recordatorios inteligentes", *notes])


def should_send_idle_reminder(key: str = "idle_reminder", cooldown_hours: int = REMINDER_COOLDOWN_HOURS) -> bool:
    """Devuelve True solo si toca avisar de recordatorios sin novedades.

    Evita enviar ntfy cada vez que el cron no encuentra correos nuevos, que era
    lo que podía provocar 429 en ntfy durante las pruebas.
    """
    now = datetime.now()
    last_raw = get_setting(key)
    if last_raw:
        try:
            last = datetime.fromisoformat(last_raw)
            if now - last < timedelta(hours=cooldown_hours):
                return False
        except ValueError:
            pass
    set_setting(key, now.isoformat(timespec="seconds"))
    return True


def append_footer(message: str) -> str:
    blocks = [message]
    reminders = smart_reminders_text()
    if reminders:
        blocks += ["", reminders]
    blocks += ["", quick_help_text()]
    return "\n".join(blocks)
