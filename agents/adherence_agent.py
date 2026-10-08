"""Adherence Agent: handles Taken/Snooze/Skip, detects missed doses, alerts the caregiver, computes stats."""
from __future__ import annotations

from datetime import datetime, timedelta

import config
import db
import notifier


def take(dose_id: int, now: datetime | None = None) -> None:
    now = now or config.now()
    d = db.get_dose(dose_id)
    db.update_dose(dose_id, status="taken", acted_at=db.iso(now))
    db.log_event("Adherence", f"{d['drug']} marked TAKEN.")


def skip(dose_id: int, now: datetime | None = None) -> None:
    now = now or config.now()
    d = db.get_dose(dose_id)
    db.update_dose(dose_id, status="skipped", acted_at=db.iso(now))
    db.log_event("Adherence", f"{d['drug']} marked SKIPPED.")


def snooze(dose_id: int, demo: bool = False, now: datetime | None = None) -> None:
    now = now or config.now()
    secs = config.timings(demo)["snooze"]
    d = db.get_dose(dose_id)
    db.update_dose(
        dose_id,
        scheduled_time=db.iso(now + timedelta(seconds=secs)),
        snooze_count=d["snooze_count"] + 1,
        emailed=0,  # allow a fresh reminder when the snooze ends
    )
    db.log_event("Adherence", f"{d['drug']} snoozed for {secs // 60 if secs >= 60 else secs}{' min' if secs >= 60 else ' sec'}.")


def sweep(demo: bool = False, now: datetime | None = None) -> int:
    """Mark doses that stayed unanswered too long as MISSED; then check for a miss streak."""
    now = now or config.now()
    cutoff = now - timedelta(seconds=config.timings(demo)["missed_after"])
    rows = db.overdue_before(db.iso(cutoff))
    for d in rows:
        db.update_dose(d["id"], status="missed", acted_at=db.iso(now))
        db.log_event("Adherence", f"{d['drug']} ({d['scheduled_time'][11:16]}) marked MISSED - no response.")
    if rows:
        _check_caregiver()
    return len(rows)


def _check_caregiver() -> bool:
    """Alert once when the two most recent resolved doses were both missed."""
    last = db.last_resolved(2)
    if len(last) < 2 or any(r["status"] != "missed" for r in last) or any(r["alerted"] for r in last):
        return False
    db.update_dose(last[0]["id"], alerted=1)
    user = db.get_user()
    names = ", ".join(f"{r['drug']} ({r['scheduled_time'][11:16]})" for r in reversed(last))
    db.log_event("Adherence", f"CAREGIVER ALERT: 2 consecutive missed doses - {names}.")
    if user["caregiver_email"]:
        ok, info = notifier.send_email(
            user["caregiver_email"],
            f"Missed medicine alert for {user['name']}",
            f"{user['name']} has missed 2 doses in a row: {names}.\nPlease check in.\n(Reminder tool only - not medical advice.)",
        )
        db.log_event("Adherence", f"Email to caregiver: {info}.")
    else:
        db.log_event("Adherence", "No caregiver email set - alert shown in app only.")
    return True


def stats() -> dict:
    rows = db.resolved_doses()
    n = {s: sum(1 for r in rows if r["status"] == s) for s in ("taken", "skipped", "missed")}
    total = sum(n.values())
    return {**n, "total": total, "adherence_pct": round(100 * n["taken"] / total) if total else None}
