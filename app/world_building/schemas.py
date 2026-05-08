# schemas.py
"""Pydantic schemas used to validate structured LLM responses for the
world-building pipeline.

All datetime-like fields are accepted as strings on the wire (LLMs are
unreliable at producing native datetimes) and parsed with a permissive
validator. Gender is similarly accepted as a string and normalized to a
boolean (True == male, False == female, None == unknown) to match the
existing ORM column type.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


def _parse_datetime(value):
    if value is None or isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def _parse_gender(value):
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, str):
        return {"male": True, "female": False}.get(value.strip().lower())
    return None


class SkillOut(BaseModel):
    name: str
    description: str = ""


class StatusOut(BaseModel):
    name: str
    description: str = ""
    type: str = "buff"
    duration: float = 0.0


class ItemOut(BaseModel):
    name: str
    description: str = ""
    type: str = "misc"
    value: float = 0.0
    weight: float = 0.0
    quantity: int = 1
    condition: float = 100.0


class EventOut(BaseModel):
    name: str
    description: str = ""
    type: str = "encounter"
    role: str = "participant"


class RelationshipOut(BaseModel):
    type: str = "acquaintance"
    attraction: int = 5
    respect: int = 5
    trust: int = 5
    familiarity: int = 0
    anger: int = 5
    fear: int = 5


class _CharacterCoreMixin(BaseModel):
    name: str
    date_of_birth: Optional[datetime] = None
    race: Optional[str] = None
    gender: Optional[bool] = None

    @field_validator("date_of_birth", mode="before")
    @classmethod
    def _normalize_dob(cls, v):
        return _parse_datetime(v)

    @field_validator("gender", mode="before")
    @classmethod
    def _normalize_gender(cls, v):
        return _parse_gender(v)


class MainCharacterOut(_CharacterCoreMixin):
    """Batched main-character payload: core + skills + statuses."""
    current_date_time: Optional[datetime] = None
    skills: List[SkillOut] = Field(default_factory=list)
    statuses: List[StatusOut] = Field(default_factory=list)

    @field_validator("current_date_time", mode="before")
    @classmethod
    def _normalize_current_dt(cls, v):
        return _parse_datetime(v)


class NPCOut(_CharacterCoreMixin):
    """Batched NPC payload: core + event + skills + statuses + items."""
    event: EventOut = Field(default_factory=lambda: EventOut(name="Daily life"))
    skills: List[SkillOut] = Field(default_factory=list)
    statuses: List[StatusOut] = Field(default_factory=list)
    items: List[ItemOut] = Field(default_factory=list)


class NPCListOut(BaseModel):
    npcs: List[NPCOut] = Field(default_factory=list)


class SubLocationOut(BaseModel):
    name: str
    description: str = ""
    longitude: Optional[float] = None
    latitude: Optional[float] = None
    type: Optional[str] = None
    climate: Optional[str] = None
    terrain: Optional[str] = None


class LocationOut(SubLocationOut):
    sub_locations: List[SubLocationOut] = Field(default_factory=list)


class LocationListOut(BaseModel):
    locations: List[LocationOut] = Field(default_factory=list)


class NamingThemeChoice(BaseModel):
    source: str
    theme: str


class NamingThemeSelectionOut(BaseModel):
    themes: List[NamingThemeChoice] = Field(default_factory=list)
    reasoning: Optional[str] = None
