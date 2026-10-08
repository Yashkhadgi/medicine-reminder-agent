"""Scheduler Agent: structured medicines + the user's daily routine -> concrete dose times."""
from __future__ import annotations

from datetime import datetime, time, timedelta

import config
import db
from models import SLOTS, Medicine

FOOD_OFFSET_MIN = {"before food": -30, "after food": 30, "with food": 0, "any": 0}
_ROUTINE_KEY = {"breakfast": "breakfast_time", "lunch": "lunch_time", "dinner": "dinner_time", "bedtime": "bedtime"}


def _hhmm(s: str) -> time:
    h, m = s.split(":")[:2]
    return time(int(h), int(m))


def slot_clock(user: dict, slot: str, food_relation: str) -> time:
    base = datetime.combine(datetime(2000, 1, 1), _hhmm(user[_ROUTINE_KEY[slot]]))
    offset = 0 if slot == "bedtime" else FOOD_OFFSET_MIN.get(food_relation, 0)
    return (base + timedelta(minutes=offset)).time()


def build_schedule(meds: list[Medicine], user: dict, demo: bool = False, now: datetime | None = None) -> list[dict]:
    """Returns [{drug, dose, food_relation, scheduled_time(datetime)}] sorted by time."""
    now = now or config.now()
    out: list[dict] = []

    if demo:  # compress: first dose in ~30s, then one every ~30s (capped)
        t = config.timings(True)
        pairs = [(m, s) for m in meds for s in sorted(m.slots, key=SLOTS.index)]
        pairs.sort(key=lambda p: (SLOTS.index(p[1]), p[0].drug))
        for i, (m, s) in enumerate(pairs[: t["max_doses"]]):
            when = now + timedelta(seconds=t["first_dose"] + i * t["gap"])
            out.append({"drug": m.drug, "dose": m.dose, "food_relation": m.food_relation, "scheduled_time": when})
        return sorted(out, key=lambda r: r["scheduled_time"])

    for m in meds:
        slots = sorted(m.slots, key=SLOTS.index)
        today_left = [s for s in slots if datetime.combine(now.date(), slot_clock(user, s, m.food_relation)) > now]
        start = 0 if today_left else 1  # nothing left today -> start tomorrow so the course length stays right
        for day in range(start, start + m.duration_days):
            date = now.date() + timedelta(days=day)
            for s in slots:
                when = datetime.combine(date, slot_clock(user, s, m.food_relation))
                if when > now:
                    out.append({"drug": m.drug, "dose": m.dose, "food_relation": m.food_relation, "scheduled_time": when})
    return sorted(out, key=lambda r: r["scheduled_time"])


def create_schedule(prescription_id: int, meds: list[Medicine], user: dict, demo: bool = False, now: datetime | None = None) -> int:
    rows = build_schedule(meds, user, demo, now)
    db.add_doses(
        [{**r, "prescription_id": prescription_id, "scheduled_time": db.iso(r["scheduled_time"])} for r in rows]
    )
    db.log_event("Scheduler", f"Created {len(rows)} dose reminder(s) for {len(meds)} medicine(s)" + (" (demo timing)." if demo else "."))
    return len(rows)
