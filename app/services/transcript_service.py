# transcript_service.py
"""Single API for reading and writing the per-seed narrative transcript.

Every text the user sees in the game panel (world-building progress lines,
narrator descriptions, the player's own commands, dialogue, combat output,
system notices) is persisted as a ``TranscriptEntry`` keyed by ``seed_id``
so the panel can be replayed on refresh or resume.

Callers should go through this service rather than instantiating
``TranscriptEntry`` directly so the write path (short-lived session,
swallowed logging failures) and the read path (canonical ordering,
metadata decoding) stay in one place.
"""
from __future__ import annotations

import json
from typing import Optional

from app.orm import TranscriptEntry


# Known transcript kinds. The set is intentionally open -- callers may pass
# any string -- but listing the standard values here keeps usage consistent
# across the codebase and gives the front end a stable vocabulary to style on.
KIND_WORLD_BUILDING = 'world_building'
KIND_NARRATION = 'narration'
KIND_PLAYER_INPUT = 'player_input'
KIND_DIALOGUE = 'dialogue'
KIND_COMBAT = 'combat'
KIND_SYSTEM = 'system'
KIND_QUEST = 'quest'


def add_entry(
    session_factory,
    seed_id,
    kind,
    text,
    *,
    turn=None,
    speaker=None,
    meta=None,
    status='info',
):
    """Persist a single transcript entry using a fresh short-lived session.

    A dedicated session is used (rather than reusing a caller-provided one)
    so that logging never flushes or commits unrelated work that the caller
    may have staged on its own session. Failures are swallowed: the
    transcript is a UX nicety and must not break gameplay.

    Args:
        session_factory: Callable returning a new SQLAlchemy session.
        seed_id: The seed this entry belongs to.
        kind: One of the ``KIND_*`` constants (or any short string).
        text: The human-readable line to display.
        turn: Optional game turn the entry belongs to.
        speaker: Optional speaker name (NPC, player, "Narrator", ...).
        meta: Optional dict of kind-specific structured data; serialised to
            JSON in the ``meta`` column.
        status: Soft severity hint folded into ``meta`` when not 'info'.
            Used today by the world-builder progress callback to flag
            'success' and 'error' lines.

    Returns:
        The persisted ``TranscriptEntry`` (detached from its session) on
        success, or ``None`` if the write failed.
    """
    payload = dict(meta) if meta else {}
    if status and status != 'info':
        payload.setdefault('status', status)
    meta_json = json.dumps(payload) if payload else None

    try:
        session = session_factory()
    except Exception:
        return None
    try:
        entry = TranscriptEntry(
            seed_id=seed_id,
            turn=turn,
            kind=kind,
            speaker=speaker,
            text=text,
            meta=meta_json,
        )
        session.add(entry)
        session.commit()
        session.refresh(entry)
        session.expunge(entry)
        return entry
    except Exception:
        try:
            session.rollback()
        except Exception:
            pass
        return None
    finally:
        try:
            session.close()
        except Exception:
            pass


def list_for_seed(session, seed_id):
    """Return all transcript entries for ``seed_id`` in display order.

    Ordering is by primary key, which is monotonically increasing within a
    seed and so matches the order rows were written by ``add_entry``.

    Each item is a plain dict suitable for direct JSON serialisation by the
    Flask routes; ``meta`` is decoded back into a dict when present.
    """
    rows = (
        session.query(TranscriptEntry)
        .filter(TranscriptEntry.seed_id == seed_id)
        .order_by(TranscriptEntry.id)
        .all()
    )
    return [_serialise(row) for row in rows]


def _serialise(entry):
    return {
        'id': entry.id,
        'turn': entry.turn,
        'kind': entry.kind,
        'speaker': entry.speaker,
        'text': entry.text,
        'meta': json.loads(entry.meta) if entry.meta else None,
        'created_at': entry.created_at.isoformat() if entry.created_at else None,
    }
