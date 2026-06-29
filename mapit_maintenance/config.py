from __future__ import annotations

import os
from pathlib import Path

DB_PATH = Path(os.getenv("MAPIT_DB", "moto_maintenance.db"))
DOWNLOAD_DIR = Path(os.getenv("MAPIT_DOWNLOAD_DIR", "downloads_mapit"))

GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS", "").strip()
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "").strip()
GMAIL_MAILBOX = os.getenv("GMAIL_MAILBOX", "INBOX").strip() or "INBOX"
MAPIT_REPORT_SEARCH = os.getenv("MAPIT_REPORT_SEARCH", '(UNSEEN FROM "mapit")')
MAPIT_COMMAND_SEARCH = os.getenv("MAPIT_COMMAND_SEARCH", '(UNSEEN SUBJECT "mapit")')
MARK_EMAIL_AS_SEEN = os.getenv("MARK_EMAIL_AS_SEEN", "1") == "1"
ARCHIVE_EMAIL_AFTER_SUCCESS = os.getenv("ARCHIVE_EMAIL_AFTER_SUCCESS", "0") == "1"

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()
GITHUB_REPO = os.getenv("GITHUB_REPO", "").strip()
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main").strip() or "main"
GITHUB_DB_PATH = os.getenv("GITHUB_DB_PATH", "data/moto_maintenance.db").strip()

CHAIN_GREASE_INTERVAL_KM = float(os.getenv("CHAIN_GREASE_INTERVAL_KM", "1000"))
CHAIN_CLEAN_INTERVAL_KM = float(os.getenv("CHAIN_CLEAN_INTERVAL_KM", "2000"))
WHEELS_INTERVAL_KM = float(os.getenv("WHEELS_INTERVAL_KM", "4000"))
REVISION_INTERVAL_KM = float(os.getenv("REVISION_INTERVAL_KM", os.getenv("OIL_INTERVAL_KM", "120000")))
OIL_INTERVAL_KM = REVISION_INTERVAL_KM  # compatibilidad interna antigua
REPORT_REMINDER_DAYS = int(os.getenv("REPORT_REMINDER_DAYS", "7"))
REMINDER_COOLDOWN_HOURS = int(os.getenv("REMINDER_COOLDOWN_HOURS", "48"))

NTFY_TOPIC = os.getenv("NTFY_TOPIC", "").strip()
# Sin emojis en cabecera: algunos clientes/servidores codifican headers como latin-1.
NTFY_TITLE = os.getenv("NTFY_TITLE", "Mapit mantenimiento").strip() or "Mapit mantenimiento"
NTFY_NOTIFY_EMPTY = os.getenv("NTFY_NOTIFY_EMPTY", "0") == "1"
