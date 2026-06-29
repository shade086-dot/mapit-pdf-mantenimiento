from __future__ import annotations

import re
from typing import Optional

from .maintenance import (
    add_event,
    add_km_offset,
    build_history_text,
    build_month_stats_text,
    build_status_text,
    set_counter,
    set_real_odometer_km,
)
from .reminders import append_footer


def normalize_subject(subject: str) -> str:
    subject = re.sub(r"^(re|fw|fwd):\s*", "", subject or "", flags=re.I).strip().lower()
    subject = subject.replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u")
    return re.sub(r"\s+", " ", subject)


def parse_odometer(text: str) -> Optional[float]:
    m = re.search(r"(?:km|kilometros|kilómetros)\D*(\d{3,7}(?:[\.,]\d+)?)", text or "", re.I)
    if not m:
        m = re.search(r"(\d{3,7}(?:[\.,]\d+)?)\s*km", text or "", re.I)
    return float(m.group(1).replace(".", "").replace(",", ".")) if m else None


def parse_number(value: str) -> Optional[float]:
    if value is None:
        return None
    m = re.search(r"[-+]?\d+(?:[\.,]\d+)?", str(value))
    if not m:
        return None
    return float(m.group(0).replace(",", "."))


def parse_key_values(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([a-záéíóúñ_ -]+)\s*[:=]\s*(.+)$", line, re.I)
        if not m:
            continue
        key = normalize_subject(m.group(1)).replace(" ", "_")
        out[key] = m.group(2).strip()
    return out


def extract_number_from_command(command: str, names: tuple[str, ...]) -> Optional[float]:
    for name in names:
        if command.startswith(name):
            return parse_number(command.replace(name, "", 1))
    return None


def build_help_text() -> str:
    return "\n".join([
        "📌 Comandos Mapit por email",
        "Asunto: mapit estado",
        "Asunto: mapit engrase",
        "Asunto: mapit limpieza",
        "Asunto: mapit ruedas",
        "Asunto: mapit revision  (revisión completa)",
        "Asunto: mapit historial",
        "Asunto: mapit stats",
        "",
        "Ajustes cómodos en un solo correo:",
        "Asunto: mapit actualizar",
        "Cuerpo:",
        "km=13100",
        "engrase=500",
        "limpieza=1200",
        "ruedas=3000",
        "revision=3500",
        "",
        "Valores = km desde el último mantenimiento.",
        "Ejemplo: engrase=500 significa que quedan 500 km para engrasar.",
        "",
        "Otros:",
        "Asunto: mapit km 13100",
        "Asunto: mapit ajuste -135",
        "Asunto: mapit contador engrase 500",
        "Asunto: mapit contador limpieza 1200",
        "Asunto: mapit contador ruedas 3000",
        "Asunto: mapit contador revision 3500",
    ])


def process_update_command(body: str) -> str:
    values = parse_key_values(body)
    actions: list[str] = []
    note = values.get("nota", "")

    if "km" in values or "odometro" in values or "odómetro" in values:
        km_value = parse_number(values.get("km") or values.get("odometro") or values.get("odómetro") or "")
        if km_value is not None:
            offset = set_real_odometer_km(km_value)
            actions.append(f"Km reales fijados a {km_value:.0f} km (ajuste {offset:+.1f} km)")

    if "ajuste" in values or "offset" in values:
        delta = parse_number(values.get("ajuste") or values.get("offset") or "")
        if delta is not None:
            offset = add_km_offset(delta)
            actions.append(f"Ajuste Mapit aplicado: {delta:+.1f} km (total ajuste {offset:+.1f} km)")

    if "engrase" in values or "cadena" in values:
        km_done = parse_number(values.get("engrase") or values.get("cadena") or "")
        if km_done is not None:
            set_counter("engrase_cadena", km_done, note or f"Engrase fijado a {km_done:.0f} km")
            actions.append(f"Contador engrase fijado a {km_done:.0f} km")

    if "limpieza" in values or "limpieza_cadena" in values:
        km_done = parse_number(values.get("limpieza") or values.get("limpieza_cadena") or "")
        if km_done is not None:
            set_counter("limpieza_cadena", km_done, note or f"Limpieza fijada a {km_done:.0f} km")
            actions.append(f"Contador limpieza fijado a {km_done:.0f} km")

    if "ruedas" in values or "neumaticos" in values or "neumáticos" in values:
        km_done = parse_number(values.get("ruedas") or values.get("neumaticos") or values.get("neumáticos") or "")
        if km_done is not None:
            set_counter("ruedas", km_done, note or f"Ruedas fijadas a {km_done:.0f} km")
            actions.append(f"Contador ruedas fijado a {km_done:.0f} km")

    if "revision" in values or "revisión" in values or "mantenimiento" in values:
        km_done = parse_number(values.get("revision") or values.get("revisión") or values.get("mantenimiento") or "")
        if km_done is not None:
            set_counter("aceite", km_done, note or f"Revisión fijada a {km_done:.0f} km")
            actions.append(f"Contador revisión fijado a {km_done:.0f} km")

    if not actions:
        return append_footer("No he encontrado cambios en el cuerpo. Usa por ejemplo:\nkm=13100\nengrase=500\nlimpieza=1200\nruedas=3000\nrevision=3500")

    return append_footer("✅ Mapit actualizado\n" + "\n".join(f"- {a}" for a in actions) + "\n\n" + build_status_text())


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

    if command in ("actualizar", "sync", "sincronizar"):
        return True, process_update_command(note), "actualizar"

    km_cmd = extract_number_from_command(command, ("km", "odometro", "odómetro"))
    if km_cmd is not None:
        offset = set_real_odometer_km(km_cmd)
        return True, append_footer(f"✅ Km reales fijados a {km_cmd:.0f} km (ajuste {offset:+.1f} km).\n\n" + build_status_text()), "km"

    ajuste_cmd = extract_number_from_command(command, ("ajuste", "offset"))
    if ajuste_cmd is not None:
        offset = add_km_offset(ajuste_cmd)
        return True, append_footer(f"✅ Ajuste aplicado: {ajuste_cmd:+.1f} km (total ajuste {offset:+.1f} km).\n\n" + build_status_text()), "ajuste"

    m = re.match(r"contador\s+(engrase|cadena|limpieza|limpieza cadena|ruedas|neumaticos|neumáticos|revision|revisión|mantenimiento)\s+(.+)$", command)
    if m:
        km_done = parse_number(m.group(2))
        if km_done is not None:
            key = m.group(1)
            if key in ("engrase", "cadena"):
                event, label = "engrase_cadena", "engrase"
            elif key in ("limpieza", "limpieza cadena"):
                event, label = "limpieza_cadena", "limpieza"
            elif key in ("ruedas", "neumaticos", "neumáticos"):
                event, label = "ruedas", "ruedas"
            else:
                event, label = "aceite", "revisión"
            set_counter(event, km_done, note or f"Contador {label} fijado a {km_done:.0f} km")
            return True, append_footer(f"✅ Contador {label} fijado a {km_done:.0f} km.\n\n" + build_status_text()), f"contador_{label}"

    if command in ("engrase", "engrase cadena", "cadena"):
        add_event("engrase_cadena", odo, note)
        return True, append_footer("✅ Engrase de cadena registrado.\n\n" + build_status_text()), "engrase_cadena"
    if command in ("limpieza", "limpieza cadena", "limpiar cadena"):
        add_event("limpieza_cadena", odo, note)
        return True, append_footer("✅ Limpieza de cadena registrada.\n\n" + build_status_text()), "limpieza_cadena"
    if command in ("ruedas", "neumaticos", "neumáticos"):
        add_event("ruedas", odo, note or "Revisión ruedas")
        return True, append_footer("✅ Ruedas registradas.\n\n" + build_status_text()), "ruedas"

    if command in ("revision", "revisión", "mantenimiento", "mantenimiento completo"):
        add_event("revision", odo, note or "Revisión general")
        add_event("aceite", odo, note or "Revisión completa")
        return True, append_footer("✅ Revisión completa registrada.\n\n" + build_status_text()), "revision"

    if command in ("aceite", "cambio aceite"):
        add_event("aceite", odo, note or "Cambio aceite")
        return True, append_footer("✅ Revisión registrada.\n\n" + build_status_text()), "aceite"
    if command == "itv":
        add_event("itv", odo, note or "ITV")
        return True, append_footer("✅ ITV registrada.\n\n" + build_history_text(5)), "itv"
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
