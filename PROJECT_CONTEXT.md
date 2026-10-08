# Project context (for AI coding agents / future changes)

**What:** Streamlit app "MedMinder AI". Four agents (Parser, Scheduler, Reminder, Adherence) in `agents/`, orchestrated by `app.py`.

**How it runs:** `app.py` uses `streamlit-autorefresh` (5 s). Each rerun: `adherence_agent.sweep()` then `reminder_agent.check_due()` then render. No background threads or schedulers (they are unreliable on Streamlit Cloud).

**Data:** SQLite (`db.py`). Tables: users (single row id=1), prescriptions, doses, events (agent log). Times are naive local strings `YYYY-MM-DD HH:MM:SS` in `config.TIMEZONE` (default Asia/Kolkata). Always use `config.now()` and `db.iso()`.

**Dose status:** pending → taken | skipped | missed. Snooze keeps `pending` and pushes `scheduled_time` forward.

**Conventions**
- The LLM only produces JSON validated by `models.Prescription`. Keep parsing logic in `parser_agent.py`; keep `rule_based_parse` as the offline fallback.
- Demo mode timings live in `config.timings(demo)`. Agents take `now=` so they are testable.
- LLM providers are plain `requests` calls in `llm.py`; add a provider by adding a `_call_x` function and registering it in `PROVIDERS` and `KEY_NAMES`.
- Secrets via `.env` or Streamlit secrets through `config.get_secret()`. Never hardcode keys.
- Run `pytest -q` after every change. Tests use a temp DB and need no API key.

**Known limits / ideas:** single user; no image OCR yet; weekly/alternate-day schedules are not modelled (noted in `notes`); free hosting resets the DB.
