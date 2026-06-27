from __future__ import annotations

import re
from typing import Optional

from .maintenance import add_event, build_history_text, build_month_stats_text, build_status_text
from .reminders import append_footer


def normalize_subject(subject: str) -> str:
    subject = re.sub(r"^(re|fw|fwd):\s*", "", subject or "", flags=re.I).strip().lower()
    subject = subject.replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u")
    return re.sub(r"\s+", " ", subject)


def parse_odometer(text: str) -> Optional[float]:
    m = re.search(r"(?:km|kilometros|kilómetros)\D*(\d{3,6}(?:[\.,]\d+)?)", text or "", re.I)
    if not m:
        m = re.search(r"(\d{3,6}(?:[\.,]\d+)?)\s*km", text or "", re.I)
    return float(m.group(1).replace(".", "").replace(",", ".")) if m else None


def build_help_text() -> str:
    return "\n".join([
        "📌 Comandos Mapit por email",
        "Asunto: mapit estado",
        "Asunto: mapit engrase",
        "Asunto: mapit limpieza",
        "Asunto: mapit aceite",
        "Asunto: mapit revision",
        "Asunto: mapit itv",
        "Asunto: mapit neumaticos",
        "Asunto: mapit repostaje",
        "Asunto: mapit historial",
        "Asunto: mapit stats",
        "",
        "Puedes añadir notas o km en el cuerpo del correo.",
        "Ejemplo: 18540 km + comentario del mantenimiento.",
    ])


def process_command(subject: str, body: str = "") -> tuple[bool, str, Optional[str]]:
    s = normalize_subject(subject)
    if not s.startswith("mapit"):
        return False, "", None
    command = s.replace("mapit", "", 1).strip() or "estado"
    note = (body or "").strip()
    odo = parse_odometer(note)

    if command in ("ayuda", "help", "comandos"):
        return True, append_footer(build_help_text()), "ayuda"
    if command in ("estado", "status"):
        return True, append_footer(build_status_text()), "estado"
    if command in ("historial", "history"):
        return True, append_footer(build_history_text()), "historial"
    if command in ("stats", "estadisticas", "estadísticas"):
        return True, append_footer(build_month_stats_text()), "stats"
    if command in ("engrase", "engrase cadena", "cadena"):
        add_event("engrase_cadena", odo, note)
        return True, append_footer("✅ Engrase de cadena registrado.\n\n" + build_status_text()), "engrase_cadena"
    if command in ("limpieza", "limpieza cadena", "limpiar cadena"):
        add_event("limpieza_cadena", odo, note)
        return True, append_footer("✅ Limpieza de cadena registrada.\n\n" + build_status_text()), "limpieza_cadena"
    if command in ("aceite", "cambio aceite"):
        add_event("aceite", odo, note or "Cambio aceite")
        return True, append_footer("✅ Aceite/revisión registrado.\n\n" + build_status_text()), "aceite"
    if command in ("revision", "revisión"):
        add_event("revision", odo, note or "Revisión general")
        return True, append_footer("✅ Revisión general registrada.\n\n" + build_status_text()), "revision"
    if command == "itv":
        add_event("itv", odo, note or "ITV")
        return True, append_footer("✅ ITV registrada.\n\n" + build_history_text(5)), "itv"
    if command in ("neumaticos", "neumáticos", "ruedas"):
        add_event("neumaticos", odo, note or "Cambio neumáticos")
        return True, append_footer("✅ Neumáticos registrados.\n\n" + build_history_text(5)), "neumaticos"
    if command == "repostaje":
        add_event("repostaje", odo, note or "Repostaje")
        return True, append_footer("✅ Repostaje registrado.\n\n" + build_history_text(5)), "repostaje"
    if command in ("presiones", "presion", "presión"):
        add_event("presiones", odo, note or "Presiones revisadas")
        return True, append_footer("✅ Presiones registradas.\n\n" + build_history_text(5)), "presiones"
    if command == "bateria":
        add_event("bateria", odo, note or "Batería revisada")
        return True, append_footer("✅ Batería registrada.\n\n" + build_history_text(5)), "bateria"
    if command == "seguro":
        add_event("seguro", odo, note or "Seguro")
        return True, append_footer("✅ Seguro registrado.\n\n" + build_history_text(5)), "seguro"

    help_msg = "No reconozco ese comando.\n\n" + build_help_text()
    return True, append_footer(help_msg), "desconocido"
