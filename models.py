"""Pydantic schemas: the contract between the Parser agent and everything else."""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator

SLOTS = ["breakfast", "lunch", "dinner", "bedtime"]
DEFAULT_SLOTS = {
    1: ["breakfast"],
    2: ["breakfast", "dinner"],
    3: ["breakfast", "lunch", "dinner"],
    4: ["breakfast", "lunch", "dinner", "bedtime"],
}
_ALIASES = {
    "morning": "breakfast",
    "noon": "lunch",
    "afternoon": "lunch",
    "evening": "dinner",
    "night": "dinner",
    "bed time": "bedtime",
    "sleep": "bedtime",
    "night (bedtime)": "bedtime",
}


class Medicine(BaseModel):
    drug: str
    dose: str = ""
    frequency_per_day: int = Field(1, ge=1, le=6)
    slots: list[str] = []
    food_relation: str = "any"  # before food | after food | with food | any
    duration_days: int = Field(1, ge=1, le=365)
    notes: str = ""

    @field_validator("dose", "notes", mode="before")
    @classmethod
    def _str(cls, v):
        return "" if v is None else str(v)

    @field_validator("frequency_per_day", "duration_days", mode="before")
    @classmethod
    def _int(cls, v):
        try:
            return max(1, int(float(v)))
        except (TypeError, ValueError):
            return 1

    @field_validator("slots", mode="before")
    @classmethod
    def _slots(cls, v):
        out = []
        for s in v or []:
            s = _ALIASES.get(str(s).lower().strip(), str(s).lower().strip())
            if s in SLOTS and s not in out:
                out.append(s)
        return out

    @field_validator("food_relation", mode="before")
    @classmethod
    def _food(cls, v):
        v = str(v or "any").lower()
        for key in ("before", "after", "with"):
            if key in v:
                return f"{key} food"
        return "any"

    @model_validator(mode="after")
    def _fill(self):
        if self.slots:
            self.frequency_per_day = len(self.slots)
        else:
            self.slots = list(DEFAULT_SLOTS[min(self.frequency_per_day, 4)])
        return self


class Prescription(BaseModel):
    medicines: list[Medicine]

    @field_validator("medicines")
    @classmethod
    def _non_empty(cls, v):
        if not v:
            raise ValueError("No medicines found")
        return v
