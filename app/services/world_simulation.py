"""Background world simulation: off-screen events between turns.

The simulator runs at most every ``MIN_INTERVAL_MINUTES`` of in-world
time; ``maybe_simulate`` is a single entry point that the turn / travel
routes call after they advance the clock. Each tick the LLM is asked
for 0-2 plausible incidents at locations the MC isn't currently in;
matched results are persisted as ``Event`` rows (with optional NPC
participants in ``EventCharacter``) so future NPC dialogue and the
narrator have something living to draw on.

Failure of the LLM call or any of the persistence steps is non-fatal:
the world simply ticks on without fresh news. Nothing here is allowed
to break the player's turn.
"""
from __future__ import annotations

import datetime
import logging

from app.orm import Character, Event, EventCharacter, Location
from app.prompt_templates import BACKGROUND_EVENTS
from app.services import time_service, transcript_service
from app.world_building.schemas import BackgroundEventsOut

log = logging.getLogger(__name__)


# Minimum in-world time between simulator runs. Two hours feels right:
# fast enough that a long traversal triggers fresh news, slow enough
# that a flurry of one-minute beats doesn't burn budget on background
# events the player will never see.
MIN_INTERVAL_MINUTES = 120

# Maximum events drafted per tick. The prompt itself caps at two but we
# still hard-clip on the persistence side so a runaway model can't
# spam the Event table on a single turn.
MAX_EVENTS_PER_TICK = 2


def should_simulate(seed):
    """Return True when enough world time has elapsed for a fresh tick."""
    if seed is None or seed.current_date_time is None:
        return False
    last = seed.last_event_sim_at
    if last is None:
        return True
    delta = (seed.current_date_time - last).total_seconds() / 60.0
    return delta >= MIN_INTERVAL_MINUTES


def maybe_simulate(db_session, seed_id, gpt_service, *, context,
                   session_factory=None):
    """Run a simulation tick when due; return ``(events, entry_dicts)``.

    ``context`` is the per-turn prompt context built by ``routes._build_turn_context``;
    we reuse its keys (seed_data, character, starting_location,
    other_locations, existing_characters, transcript, world_clock) so
    the simulator and the narrator share the same view of the world.

    ``events`` is the list of persisted ``Event`` rows (possibly empty).
    ``entry_dicts`` is a list of JSON-friendly transcript entry dicts the
    caller can append to a turn / travel response so the player sees the
    fresh news land in the same payload that triggered the simulation.
    Always swallows exceptions: the caller doesn't need to wrap this in
    a try.
    """
    from app.orm import Seed  # local import to avoid circular at module load
    seed = db_session.query(Seed).filter(Seed.id == seed_id).first()
    if not should_simulate(seed):
        return [], []

    try:
        prompt = BACKGROUND_EVENTS.format(**context)
        payload = gpt_service.get_structured(
            prompt, BackgroundEventsOut,
            max_attempts=2, temperature=0.9,
        )
    except Exception as e:
        log.warning("world_simulation: LLM call failed on seed %s: %s", seed_id, e)
        seed.last_event_sim_at = seed.current_date_time
        db_session.commit()
        return [], []

    if payload is None:
        seed.last_event_sim_at = seed.current_date_time
        db_session.commit()
        return [], []

    persisted, entry_dicts = _persist_events(
        db_session, seed_id, seed, payload.events[:MAX_EVENTS_PER_TICK],
        session_factory=session_factory,
    )
    seed.last_event_sim_at = seed.current_date_time
    seed.updated_at = datetime.datetime.now()
    db_session.commit()
    return persisted, entry_dicts


def _persist_events(db_session, seed_id, seed, events, *, session_factory):
    """Match each drafted event to existing rows and persist what fits.

    Returns ``(events, entry_dicts)`` -- the persisted ``Event`` rows and
    the JSON-friendly transcript entry dicts that the caller can splice
    into its response payload.
    """
    if not events:
        return [], []
    locations_by_name = {
        (loc.name or '').strip().lower(): loc
        for loc in db_session.query(Location)
        .filter(Location.seed_id == seed_id,
                Location.parent_id.is_(None))
        .all()
    }
    npcs_by_name = {
        (c.name or '').strip().lower(): c
        for c in db_session.query(Character)
        .filter(Character.seed_id == seed_id,
                Character.main_character == False)  # noqa: E712
        .all()
    }
    persisted = []
    entry_dicts = []
    for draft in events:
        loc = locations_by_name.get((draft.location_name or '').strip().lower())
        if loc is None:
            continue
        ev = Event(
            seed_id=seed_id, name=(draft.name or '').strip()[:64] or 'Unnamed',
            description=(draft.description or '').strip(),
            type=(draft.type or 'incident').strip()[:64],
            location_id=loc.id,
            start_date_time=seed.current_date_time,
            start_turn=seed.current_turn,
        )
        db_session.add(ev)
        db_session.flush()
        for nm in (draft.participant_names or []):
            npc = npcs_by_name.get((nm or '').strip().lower())
            if npc is None:
                continue
            db_session.add(EventCharacter(
                seed_id=seed_id, character_id=npc.id, event_id=ev.id,
                role='participant',
            ))
        persisted.append(ev)
        if session_factory is not None:
            text = f"[News from afar] {ev.name} at {loc.name}: {ev.description}"
            entry = transcript_service.add_entry(
                session_factory, seed_id,
                transcript_service.KIND_SYSTEM, text,
                turn=seed.current_turn,
                meta={'kind': 'background_event', 'event_id': ev.id,
                      'location_id': loc.id, 'event_type': ev.type},
            )
            if entry is not None:
                entry_dicts.append({
                    'id': entry.id,
                    'kind': transcript_service.KIND_SYSTEM,
                    'speaker': None,
                    'text': text,
                })
    return persisted, entry_dicts


def recent_offscreen_events(db_session, seed_id, current_loc_id, limit=4):
    """Return the most recent off-screen Events for prompt context."""
    q = (
        db_session.query(Event)
        .filter(Event.seed_id == seed_id)
        .order_by(Event.start_date_time.desc())
    )
    out = []
    for ev in q.limit(limit * 3).all():
        if current_loc_id is not None and ev.location_id == current_loc_id:
            continue
        out.append(ev)
        if len(out) >= limit:
            break
    return out
