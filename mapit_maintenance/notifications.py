from __future__ import annotations

import requests

from .config import (
    NTFY_FALLBACK,
    NTFY_TITLE,
    NTFY_TOPIC,
    PUSHOVER_DEVICE,
    PUSHOVER_ENABLED,
    PUSHOVER_PRIORITY,
    PUSHOVER_SOUND,
    PUSHOVER_TOKEN,
    PUSHOVER_USER,
)


def _trim_message(message: str, limit: int = 1000) -> str:
    text = str(message or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 2].rstrip() + "…"


def send_pushover(message: str, title: str = NTFY_TITLE, priority: str = "default") -> bool:
    if not (PUSHOVER_ENABLED and PUSHOVER_USER and PUSHOVER_TOKEN):
        return False
    prio = PUSHOVER_PRIORITY
    if priority in {"high", "urgent", "max"}:
        prio = "1"
    payload = {
        "token": PUSHOVER_TOKEN,
        "user": PUSHOVER_USER,
        "title": title or "Mapit mantenimiento",
        "message": _trim_message(message),
        "priority": prio,
    }
    if PUSHOVER_DEVICE:
        payload["device"] = PUSHOVER_DEVICE
    if PUSHOVER_SOUND:
        payload["sound"] = PUSHOVER_SOUND
    try:
        requests.post("https://api.pushover.net/1/messages.json", data=payload, timeout=20).raise_for_status()
        return True
    except Exception as exc:
        print(f"No he podido enviar Pushover: {exc}")
        return False


def send_ntfy(message: str, title: str = NTFY_TITLE, priority: str = "default") -> None:
    # Conservamos el nombre para no tocar llamadas existentes: ahora envía por Pushover primero.
    if send_pushover(message, title=title, priority=priority):
        return
    if not (NTFY_TOPIC and (NTFY_FALLBACK or not (PUSHOVER_USER and PUSHOVER_TOKEN))):
        return
    topic = NTFY_TOPIC if NTFY_TOPIC.startswith("http") else f"https://ntfy.sh/{NTFY_TOPIC}"
    headers = {
        "Title": title.encode("ascii", "ignore").decode("ascii") or "Mapit mantenimiento",
        "Priority": priority,
        "Tags": "motorcycle,wrench",
    }
    try:
        requests.post(topic, data=message.encode("utf-8"), headers=headers, timeout=20).raise_for_status()
    except Exception as exc:
        print(f"No he podido enviar ntfy: {exc}")
