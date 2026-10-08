"""SQLite storage layer. Times are stored as local 'YYYY-MM-DD HH:MM:SS' strings."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime

import config

FMT = "%Y-%m-%d %H:%M:%S"


def iso(dt: datetime) -> str:
    return dt.strftime(FMT)


def from_iso(s: str) -> datetime:
    return datetime.strptime(s, FMT)


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    name TEXT DEFAULT 'Patient',
    email TEXT DEFAULT '',
    caregiver_email TEXT DEFAULT '',
    breakfast_time TEXT DEFAULT '08:00',
    lunch_time TEXT DEFAULT '13:00',
    dinner_time TEXT DEFAULT '20:00',
    bedtime TEXT DEFAULT '22:00'
);
CREATE TABLE IF NOT EXISTS prescriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER DEFAULT 1,
    raw_text TEXT,
    parsed_json TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS doses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prescription_id INTEGER,
    drug TEXT,
    dose TEXT,
    food_relation TEXT,
    scheduled_time TEXT,
    status TEXT DEFAULT 'pending',   -- pending | taken | skipped | missed
    acted_at TEXT,
    snooze_count INTEGER DEFAULT 0,
    emailed INTEGER DEFAULT 0,
    alerted INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT,
    agent TEXT,
    message TEXT
);
"""


@contextmanager
def conn():
    c = sqlite3.connect(config.DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init_db() -> None:
    with conn() as c:
        c.executescript(SCHEMA)
        if not c.execute("SELECT 1 FROM users WHERE id=1").fetchone():
            c.execute("INSERT INTO users (id) VALUES (1)")


def reset_all() -> None:
    with conn() as c:
        c.executescript("DROP TABLE IF EXISTS doses; DROP TABLE IF EXISTS prescriptions; DROP TABLE IF EXISTS events;")
    init_db()


# ---------- users ----------
def get_user() -> dict:
    with conn() as c:
        return dict(c.execute("SELECT * FROM users WHERE id=1").fetchone())


def update_user(**fields) -> None:
    keys = ", ".join(f"{k}=?" for k in fields)
    with conn() as c:
        c.execute(f"UPDATE users SET {keys} WHERE id=1", list(fields.values()))


# ---------- prescriptions / doses ----------
def add_prescription(raw_text: str, parsed: dict) -> int:
    with conn() as c:
        cur = c.execute(
            "INSERT INTO prescriptions (raw_text, parsed_json, created_at) VALUES (?,?,?)",
            (raw_text, json.dumps(parsed), iso(config.now())),
        )
        return cur.lastrowid


def add_doses(rows: list[dict]) -> None:
    with conn() as c:
        c.executemany(
            "INSERT INTO doses (prescription_id, drug, dose, food_relation, scheduled_time) "
            "VALUES (:prescription_id, :drug, :dose, :food_relation, :scheduled_time)",
            rows,
        )


def query(sql: str, params: tuple = ()) -> list[dict]:
    with conn() as c:
        return [dict(r) for r in c.execute(sql, params).fetchall()]


def due_doses(now_iso: str) -> list[dict]:
    return query("SELECT * FROM doses WHERE status='pending' AND scheduled_time<=? ORDER BY scheduled_time", (now_iso,))


def upcoming_doses(now_iso: str, limit: int = 100) -> list[dict]:
    return query(
        "SELECT * FROM doses WHERE status='pending' AND scheduled_time>? ORDER BY scheduled_time LIMIT ?",
        (now_iso, limit),
    )


def overdue_before(cutoff_iso: str) -> list[dict]:
    return query("SELECT * FROM doses WHERE status='pending' AND scheduled_time<=?", (cutoff_iso,))


def resolved_doses() -> list[dict]:
    return query("SELECT * FROM doses WHERE status!='pending' ORDER BY scheduled_time DESC")


def last_resolved(n: int = 2) -> list[dict]:
    return query("SELECT * FROM doses WHERE status!='pending' ORDER BY scheduled_time DESC, id DESC LIMIT ?", (n,))


def get_dose(dose_id: int) -> dict | None:
    rows = query("SELECT * FROM doses WHERE id=?", (dose_id,))
    return rows[0] if rows else None


def update_dose(dose_id: int, **fields) -> None:
    keys = ", ".join(f"{k}=?" for k in fields)
    with conn() as c:
        c.execute(f"UPDATE doses SET {keys} WHERE id=?", [*fields.values(), dose_id])


# ---------- agent activity log ----------
def log_event(agent: str, message: str) -> None:
    with conn() as c:
        c.execute("INSERT INTO events (ts, agent, message) VALUES (?,?,?)", (iso(config.now()), agent, message))


def recent_events(limit: int = 100) -> list[dict]:
    return query("SELECT ts, agent, message FROM events ORDER BY id DESC LIMIT ?", (limit,))
