"""Central configuration. Reads env vars / .env first, then Streamlit secrets."""
from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()


def get_secret(name: str, default: str = "") -> str:
    val = os.environ.get(name)
    if val:
        return val
    try:
        import streamlit as st

        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:
        pass
    return default


TIMEZONE = get_secret("APP_TIMEZONE", "Asia/Kolkata")
DB_PATH = get_secret("DB_PATH", "medicine_reminder.db")
REFRESH_MS = 5000  # how often the UI re-checks for due doses


def now() -> datetime:
    """Current local time (naive) in the configured timezone."""
    return datetime.now(ZoneInfo(TIMEZONE)).replace(tzinfo=None)


def timings(demo: bool) -> dict:
    """All durations in seconds. Demo mode compresses minutes into seconds."""
    if demo:
        return {"snooze": 15, "missed_after": 45, "first_dose": 30, "gap": 30, "max_doses": 6}
    return {"snooze": 15 * 60, "missed_after": 30 * 60, "first_dose": 0, "gap": 0, "max_doses": 10**6}
