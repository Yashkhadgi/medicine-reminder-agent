"""Parser Agent: free-text prescription -> validated structured JSON.

Flow: LLM (JSON mode) -> Pydantic validation -> one self-correcting retry ->
rule-based regex fallback (so the demo still works with no API key / no internet).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

import requests

import db
import llm
from models import Medicine, Prescription

SYSTEM_PROMPT = """You are a prescription-parsing agent for a medicine reminder app.
Convert the prescription text into JSON. Output ONLY a JSON object, no prose, no markdown.

Schema:
{"medicines": [{
  "drug": string,                       // medicine name only
  "dose": string,                       // e.g. "500 mg", "" if unknown
  "frequency_per_day": integer,         // times per day
  "slots": [string],                    // subset of: "breakfast","lunch","dinner","bedtime"
  "food_relation": string,              // "before food" | "after food" | "with food" | "any"
  "duration_days": integer,             // convert weeks/months to days
  "notes": string
}]}

Rules:
- 1-0-1 means morning and night (breakfast, dinner); 1-1-1 means breakfast, lunch, dinner; 0-0-1 means bedtime.
- OD/once daily = 1 slot (breakfast unless "night"/"bedtime" is stated); BD/BID/twice = breakfast+dinner;
  TDS/TID/thrice = breakfast+lunch+dinner; QID/four times = all four slots.
- "once a week" or other non-daily schedules: put it in notes and use frequency_per_day 1.
- Never invent medicines. If duration is missing use 5 and say so in notes.
"""


@dataclass
class ParseResult:
    prescription: Prescription
    source: str  # provider name or "rule-based"
    warning: str | None = None


def extract_json(raw: str) -> dict:
    raw = re.sub(r"```(?:json)?", "", raw).strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        start, end = raw.find("["), raw.rfind("]")
    data = json.loads(raw[start : end + 1])
    return {"medicines": data} if isinstance(data, list) else data


# ---------------- rule-based fallback ----------------
_DOSE = re.compile(r"(\d+(?:\.\d+)?\s*(?:mg|mcg|µg|g|ml|iu|units?))\b", re.I)
_PREFIX = re.compile(r"^\s*(?:\d+[.)]\s*)?(?:tab(?:let)?s?\.?|cap(?:sule)?s?\.?|syp\.?|syrup|inj\.?)\s+", re.I)
_PATTERN = re.compile(r"(?<![\d.])([01])\s*-\s*([01])\s*-\s*([01])(?:\s*-\s*([01]))?(?![\d.])")
_DURATION = re.compile(r"(\d+)\s*(day|week|month)s?", re.I)


def _parse_line(line: str) -> Medicine | None:
    low = line.lower()
    m = _DOSE.search(line)
    if m:
        drug, dose = line[: m.start()], m.group(1)
    else:
        drug, dose = re.split(r"\b(?:once|twice|thrice|daily|od|bd|bid|tds|tid|qid|at|for|\d)\b", line, 1, flags=re.I)[0], ""
    drug = _PREFIX.sub("", drug).strip(" .,:-")
    drug = re.sub(r"^\d+[.)]\s*", "", drug).strip()
    if not drug or not re.search(r"[A-Za-z]{3}", drug):
        return None

    slots: list[str] = []
    freq = 1
    p = _PATTERN.search(low)
    if p:
        names = ["breakfast", "lunch", "dinner", "bedtime"] if p.group(4) is not None else ["breakfast", "lunch", "dinner"]
        slots = [names[i] for i, g in enumerate(p.groups()[: len(names)]) if g == "1"]
    elif re.search(r"\b(qid|four times|4 times|4x)\b", low):
        freq = 4
    elif re.search(r"\b(tds|tid|thrice|three times|3 times|3x)\b", low):
        freq = 3
    elif re.search(r"\b(bd|bid|twice|two times|2 times|2x)\b", low):
        freq = 2
    elif re.search(r"\b(night|bedtime|hs)\b", low):
        slots = ["bedtime"]

    food = "any"
    if re.search(r"before (food|meal|breakfast|lunch|dinner)|empty stomach", low):
        food = "before food"
    elif re.search(r"after (food|meal|breakfast|lunch|dinner)", low):
        food = "after food"
    elif re.search(r"with (food|meal)", low):
        food = "with food"

    notes, days = "", 5
    d = _DURATION.search(low)
    if d:
        days = int(d.group(1)) * {"day": 1, "week": 7, "month": 30}[d.group(2)]
    else:
        notes = "Duration not specified; defaulted to 5 days."
    if re.search(r"once a week|weekly", low):
        notes = (notes + " Weekly dose: reminders are created daily, skip extra ones.").strip()

    return Medicine(drug=drug, dose=dose, frequency_per_day=freq, slots=slots, food_relation=food, duration_days=days, notes=notes)


def rule_based_parse(text: str) -> Prescription:
    meds = []
    for line in re.split(r"[\n;]+", text):
        if line.strip():
            med = _parse_line(line)
            if med:
                meds.append(med)
    return Prescription(medicines=meds)


# ---------------- public entry point ----------------
def parse_prescription(text: str) -> ParseResult:
    text = (text or "").strip()
    if not text:
        raise ValueError("Enter a prescription first.")

    prov = llm.provider()
    warning = "No LLM API key set - used the offline rule-based parser."
    if prov != "none":
        err = None
        for _ in range(2):  # one self-correcting retry
            try:
                user = f"Prescription:\n{text}"
                if err:
                    user += f"\n\nYour previous output was invalid ({err}). Return corrected JSON only."
                presc = Prescription.model_validate(extract_json(llm.call_llm(SYSTEM_PROMPT, user)))
                db.log_event("Parser", f"Parsed {len(presc.medicines)} medicine(s) with {prov} ({llm.model_name(prov)}).")
                return ParseResult(presc, prov)
            except requests.RequestException as e:  # network/auth/quota: retrying will not help
                err = f"{type(e).__name__}"
                break
            except Exception as e:
                err = str(e)[:200]
        warning = f"LLM parsing failed ({err}) - used the offline rule-based parser instead."

    presc = rule_based_parse(text)
    db.log_event("Parser", f"Parsed {len(presc.medicines)} medicine(s) with rule-based fallback. {warning}")
    return ParseResult(presc, "rule-based", warning)
