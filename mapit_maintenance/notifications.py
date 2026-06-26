from __future__ import annotations

import requests

from .config import NTFY_TITLE, NTFY_TOPIC


def send_ntfy(message: str, title: str = NTFY_TITLE, priority: str = "default") -> None:
    if not NTFY_TOPIC:
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
