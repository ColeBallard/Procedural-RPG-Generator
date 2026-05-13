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


class MainCharacterItemsOut(BaseModel):
    """Starter inventory payload: a small list of low-power items."""
    items: List[ItemOut] = Field(default_factory=list)


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


class LocationConnectionOut(BaseModel):
    """A road / path / river edge between two settlements.

    ``from_index`` and ``to_index`` are 0-based indices into the
    ``LocationListOut.locations`` array; the persistence layer maps them
    to the freshly-inserted ``Location.id`` values. Edges are treated as
    undirected at render time, so callers may emit either order.
    """
    from_index: int
    to_index: int
    name: str = ""
    type: str = "road"


class GeographicFeatureOut(BaseModel):
    """A region- or line-shaped piece of natural geography (forest, river,
    mountain range, lake, ...).

    ``points`` is an ordered list of [longitude, latitude] pairs in the
    same arbitrary 'world units' as the settlements. ``closed`` flags the
    intent: True for area features that should render as a filled polygon
    (forest, lake, mountain_range, hills, plains, swamp, desert), False
    for line features that should render as a stroked polyline (river,
    coast). The persistence layer stores the points as JSON; the map
    widget consumes them directly.
    """
    name: str
    type: str = "forest"
    description: str = ""
    points: List[List[float]] = Field(default_factory=list)
    closed: bool = True


class LocationListOut(BaseModel):
    locations: List[LocationOut] = Field(default_factory=list)
    connections: List[LocationConnectionOut] = Field(default_factory=list)
    features: List[GeographicFeatureOut] = Field(default_factory=list)


class NamingThemeChoice(BaseModel):
    source: str
    theme: str


class NamingThemeSelectionOut(BaseModel):
    themes: List[NamingThemeChoice] = Field(default_factory=list)
    reasoning: Optional[str] = None


class TurnDialogueLineOut(BaseModel):
    """A single line of spoken dialogue produced during a turn.

    ``speaker`` is matched against ``Character.name`` (case-insensitive) at
    persistence time so the TTS layer can pick the right voice.
    """
    speaker: str
    text: str


class TurnNewCharacterOut(_CharacterCoreMixin):
    """A character the LLM introduces mid-game that doesn't exist yet.

    Reuses ``_CharacterCoreMixin`` so the gender / DOB normalisers match
    the world-building schemas. ``description`` is a short free-text hint
    forwarded to the ElevenLabs voice search at creation time.
    """
    description: str = ""


class ActionAdjudicationOut(BaseModel):
    """Arbiter ruling on a player action: DC, ability, time cost, brief rationale.

    Returned by the ``ARBITER_ADJUDICATE`` prompt before the narrator continues
    the story. ``requires_check`` is False for routine actions (looking
    around, casual remarks, walking a few paces) so the substrate skips
    the dice and treats the action as a guaranteed success. When True,
    the substrate rolls a check via ``app.services.dice_service`` against
    ``dc`` using ``ability`` and applies the verdict to the next prompt.

    Fields are deliberately small / typed so the LLM has the smallest
    possible JSON to produce; the wider narrative is left to the
    follow-up CONTINUE_NARRATIVE call.
    """
    requires_check: bool = False
    ability: str = "strength"
    dc: int = 10
    proficient: bool = False
    advantage: bool = False
    disadvantage: bool = False
    time_cost_minutes: int = 5
    reason: str = ""


class ScenarioTriggerOut(BaseModel):
    """Optional structured signal asking the substrate to start a scenario.

    Emitted alongside narration when the model wants to swap the free-form
    turn loop for a structured interaction. ``kind`` is matched against the
    handler registry in ``app/scenarios``; ``participants`` is a list of
    character names (matched case-insensitively against ``Character.name``)
    that should be enrolled with non-player roles. ``reason`` is a one-line
    human hint shown in the scenario summary.
    """
    kind: str
    participants: List[str] = Field(default_factory=list)
    reason: str = ""


class TurnResponseOut(BaseModel):
    """Structured continuation produced by the per-turn LLM call.

    ``narration`` holds prose from the narrator only; any character speech
    must live in ``dialogue`` so each line is attributed to the speaking
    character (and rendered with their voice). ``new_characters`` lets the
    LLM declare characters the player has just met for the first time so
    they get persisted and assigned a voice. ``scenario_trigger`` is an
    optional handoff to a structured scenario (battle, dialogue, trade, ...)
    routed by ``app/scenarios`` instead of the free-form turn loop.
    ``time_cost_minutes`` is the in-world time the action consumed, fed
    into the world clock; the substrate falls back to a default when it
    is omitted so the clock keeps ticking on quick beats.
    """
    narration: str = ""
    dialogue: List[TurnDialogueLineOut] = Field(default_factory=list)
    new_characters: List[TurnNewCharacterOut] = Field(default_factory=list)
    scenario_trigger: Optional[ScenarioTriggerOut] = None
    time_cost_minutes: Optional[int] = None


class BackgroundEventOut(BaseModel):
    """A single off-screen world event drafted by the autonomous simulator.

    ``location_name`` must match an existing top-level Location row for
    this seed (the simulator looks them up case-insensitively); entries
    that don't match are dropped on persist. ``participant_names`` is an
    optional list of existing NPC names involved as actors / victims;
    entries that don't match are also dropped silently.
    """
    name: str
    description: str = ""
    type: str = "incident"
    location_name: str = ""
    participant_names: List[str] = Field(default_factory=list)


class BackgroundEventsOut(BaseModel):
    """Container for the per-tick autonomous-events payload."""
    events: List[BackgroundEventOut] = Field(default_factory=list)
