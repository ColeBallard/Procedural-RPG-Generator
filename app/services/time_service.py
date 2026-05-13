"""Single source of truth for advancing in-world time.

The game's narrative is turn-based but the underlying world is
time-based: every action carries a minute cost so two characters in
different places can be reasoned about on the same shared clock. The
DM-adjudication loop attaches a ``time_cost_minutes`` to each player
action and passes it to ``advance_time`` after the action resolves;
travel and scenarios do the same when they consume time.

``Seed.current_date_time`` is the canonical world clock. It is bumped
forward in place so existing code paths that read it (the world payload,
the prompt context) keep working without further changes.
"""
from __future__ import annotations

import datetime
from typing import Optional


# Sensible defaults so the rest of the codebase has a single place to
# look up "how much time does X take" without baking magic numbers into
# scattered call sites. The DM may override on a per-action basis.
DEFAULT_TURN_MINUTES = 5
DEFAULT_TRAVEL_MINUTES_PER_KM = 12   # ~5 km/h walking pace
DEFAULT_REST_MINUTES = 60
MIN_TURN_MINUTES = 0
MAX_TURN_MINUTES = 60 * 24 * 7       # cap a single resolution at a week


def now_world(seed) -> Optional[datetime.datetime]:
    """Return the seed's current in-world datetime (or ``None``)."""
    return seed.current_date_time if seed is not None else None


def advance_time(db_session, seed, minutes):
    """Advance ``seed.current_date_time`` by ``minutes`` and persist.

    Negative deltas are clamped to zero (time only moves forward) and
    extreme values are capped to a week so a hallucinating LLM cannot
    skip the calendar to a far-future date in a single turn. Returns the
    new datetime; safe to call when the seed has no clock yet (no-op).
    """
    if seed is None or seed.current_date_time is None:
        return None
    delta = max(MIN_TURN_MINUTES, min(MAX_TURN_MINUTES, int(minutes or 0)))
    if delta == 0:
        return seed.current_date_time
    seed.current_date_time = seed.current_date_time + datetime.timedelta(
        minutes=delta)
    seed.updated_at = datetime.datetime.now()
    db_session.add(seed)
    db_session.flush()
    return seed.current_date_time


def format_clock(dt):
    """Render an in-world datetime for transcript / UI surfaces.

    Format: ``YYYY-MM-DD HH:MM`` with a ``time-of-day`` hint appended so
    the LLM (and the player) can reason about lighting, NPC schedules,
    etc. without having to redo the math.
    """
    if dt is None:
        return ''
    tod = time_of_day(dt)
    return f"{dt.strftime('%Y-%m-%d %H:%M')} ({tod})"


def time_of_day(dt):
    """Bucket the hour into a coarse label used by prompts and the UI."""
    if dt is None:
        return 'unknown'
    h = dt.hour
    if 5 <= h < 8:
        return 'dawn'
    if 8 <= h < 12:
        return 'morning'
    if 12 <= h < 14:
        return 'noon'
    if 14 <= h < 18:
        return 'afternoon'
    if 18 <= h < 21:
        return 'evening'
    if 21 <= h < 24:
        return 'night'
    return 'late night'


def serialize_clock(seed):
    """Return the JSON-friendly clock payload sent to the frontend."""
    dt = now_world(seed)
    if dt is None:
        return {
            'iso': None,
            'display': '',
            'time_of_day': 'unknown',
            'minute_of_day': None,
            'day_of_year': None,
        }
    return {
        'iso': dt.isoformat(timespec='minutes'),
        'display': format_clock(dt),
        'time_of_day': time_of_day(dt),
        'minute_of_day': dt.hour * 60 + dt.minute,
        'day_of_year': dt.timetuple().tm_yday,
    }
