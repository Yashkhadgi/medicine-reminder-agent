"""Reminder Agent: finds doses that are due and fires notifications (UI banner + optional email)."""
from __future__ import annotations

from datetime import datetime

import config
import db
import notifier


def check_due(now: datetime | None = None) -> list[dict]:
    now = now or config.now()
    due = db.due_doses(db.iso(now))
    user = db.get_user()
    for d in due:
        if d["emailed"]:
            continue
        db.update_dose(d["id"], emailed=1)  # mark first, so a slow/failed email never repeats every refresh
        db.log_event("Reminder", f"Dose due: {d['drug']} {d['dose']} (scheduled {d['scheduled_time'][11:16]}).")
        if user["email"] and notifier.email_configured():
            ok, info = notifier.send_email(
                user["email"],
                f"Time to take {d['drug']}",
                f"Hi {user['name']}, it's time for {d['drug']} {d['dose']} ({d['food_relation']}).\n\n"
                "Open the app and tap Taken, Snooze or Skip.\n(Reminder tool only - not medical advice.)",
            )
            db.log_event("Reminder", f"Email to patient: {info}.")
    return due
