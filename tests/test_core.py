"""Run with:  pytest -q   (uses a temporary DB, no API key needed)"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

import config
import db
import llm
from agents import adherence_agent as adh
from agents import parser_agent, reminder_agent, scheduler_agent
from models import Medicine

NOW = datetime(2026, 9, 20, 7, 0, 0)


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    for k in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GROQ_API_KEY", "LLM_PROVIDER"):
        monkeypatch.delenv(k, raising=False)
    db.init_db()


def test_rule_parser_basic():
    p = parser_agent.rule_based_parse("Paracetamol 500mg twice daily after food for 5 days")
    m = p.medicines[0]
    assert m.drug == "Paracetamol" and m.dose == "500mg"
    assert m.slots == ["breakfast", "dinner"] and m.food_relation == "after food" and m.duration_days == 5


def test_rule_parser_patterns():
    p = parser_agent.rule_based_parse(
        "Tab Metformin 500 mg 1-0-1 after meals for 30 days\n"
        "Atorvastatin 10 mg once daily at night for 1 month\n"
        "Amoxicillin 250mg three times a day for 1 week"
    )
    a, b, c = p.medicines
    assert a.drug == "Metformin" and a.slots == ["breakfast", "dinner"]
    assert b.slots == ["bedtime"] and b.duration_days == 30
    assert c.slots == ["breakfast", "lunch", "dinner"] and c.duration_days == 7


def test_parse_falls_back_without_key(monkeypatch):
    monkeypatch.setattr(llm, "provider", lambda: "none")
    r = parser_agent.parse_prescription("Cetirizine 10mg at bedtime for 5 days")
    assert r.source == "rule-based" and r.prescription.medicines[0].slots == ["bedtime"]


def test_extract_json_handles_fences():
    assert parser_agent.extract_json('```json\n{"medicines": []}\n```') == {"medicines": []}


def test_schedule_times_and_offsets():
    user = db.get_user()
    med = Medicine(drug="X", frequency_per_day=2, food_relation="after food", duration_days=2)
    rows = scheduler_agent.build_schedule([med], user, now=NOW)
    times = [r["scheduled_time"].strftime("%d %H:%M") for r in rows]
    assert times == ["20 08:30", "20 20:30", "21 08:30", "21 20:30"]


def test_schedule_starts_tomorrow_if_today_is_over():
    user = db.get_user()
    med = Medicine(drug="X", frequency_per_day=1, duration_days=3)
    rows = scheduler_agent.build_schedule([med], user, now=datetime(2026, 9, 20, 23, 0))
    assert len(rows) == 3 and rows[0]["scheduled_time"].day == 21


def test_demo_schedule_is_compressed():
    med = Medicine(drug="X", frequency_per_day=3, duration_days=10)
    rows = scheduler_agent.build_schedule([med], db.get_user(), demo=True, now=NOW)
    assert len(rows) == 3
    assert (rows[0]["scheduled_time"] - NOW).seconds == 30


def _make(n=3):
    med = Medicine(drug="Drug", frequency_per_day=n, duration_days=1)
    pid = db.add_prescription("x", {})
    scheduler_agent.create_schedule(pid, [med], db.get_user(), demo=True, now=NOW)


def test_due_take_snooze_skip():
    _make()
    assert reminder_agent.check_due(NOW) == []
    due = reminder_agent.check_due(NOW + timedelta(seconds=35))
    assert len(due) == 1
    adh.snooze(due[0]["id"], demo=True, now=NOW + timedelta(seconds=35))
    assert reminder_agent.check_due(NOW + timedelta(seconds=40)) == []
    assert len(reminder_agent.check_due(NOW + timedelta(seconds=55))) == 1
    adh.take(due[0]["id"], NOW + timedelta(seconds=56))
    assert adh.stats()["taken"] == 1


def test_missed_and_caregiver_alert_once():
    _make(3)  # due at +30s, +60s, +90s
    late = NOW + timedelta(seconds=200)  # everything overdue beyond 45s grace
    assert adh.sweep(demo=True, now=late) == 3
    alerts = [e for e in db.recent_events() if "CAREGIVER ALERT" in e["message"]]
    assert len(alerts) == 1
    assert adh.stats() == {"taken": 0, "skipped": 0, "missed": 3, "total": 3, "adherence_pct": 0}


def test_streak_after_a_taken_dose_still_alerts():
    _make(3)
    first = db.due_doses(db.iso(NOW + timedelta(seconds=35)))[0]
    adh.take(first["id"], NOW + timedelta(seconds=36))
    adh.sweep(demo=True, now=NOW + timedelta(seconds=200))  # 2 missed after a taken one -> a 2-miss streak
    assert adh.stats()["taken"] == 1 and adh.stats()["missed"] == 2
    assert any("CAREGIVER ALERT" in e["message"] for e in db.recent_events())
