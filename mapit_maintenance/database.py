from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Optional

from .config import DB_PATH


def db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
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

            CREATE TABLE IF NOT EXISTS processed_emails (
                message_uid TEXT PRIMARY KEY,
                kind TEXT NOT NULL DEFAULT 'report',
                subject TEXT,
                processed_at TEXT NOT NULL,
                pdf_name TEXT,
                inserted_trips INTEGER DEFAULT 0,
                added_km REAL DEFAULT 0,
                command TEXT
            );

            CREATE TABLE IF NOT EXISTS reminder_state (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT NOT NULL
            );
            """
        )


def get_setting(key: str) -> Optional[str]:
    init_db()
    with db() as con:
        row = con.execute("SELECT value FROM reminder_state WHERE key = ?", (key,)).fetchone()
        return None if row is None else row["value"]


def set_setting(key: str, value: str) -> None:
    init_db()
    with db() as con:
        con.execute(
            "INSERT OR REPLACE INTO reminder_state (key, value, updated_at) VALUES (?, ?, ?)",
            (key, value, datetime.now().isoformat(timespec="seconds")),
        )
