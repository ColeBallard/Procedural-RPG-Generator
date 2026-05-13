"""Base class + shared helpers for scenario handlers.

``ScenarioHandler`` is the contract every kind (dialogue, battle, trade)
implements. The substrate (routes + frontend) only ever talks to handlers
through this surface so adding a new kind is a single ``handler = MyKind()``
registration in ``app/scenarios/__init__.py``.

State persistence is centralised here: the ``state`` JSON column on
``Scenario`` is opaque to the substrate, so handlers go through
``load_state`` / ``save_state`` to read and rewrite it without each kind
having to repeat the json plumbing.
"""
from __future__ import annotations

import datetime
import json
from abc import ABC, abstractmethod

from app.orm import Character, Scenario, ScenarioParticipant


KIND_DIALOGUE = 'dialogue'
KIND_BATTLE = 'battle'
KIND_TRADE = 'trade'


# --- Shared helpers --------------------------------------------------------

def active_scenario_for(db_session, seed_id):
    """Return the seed's currently-active scenario, if any.

    Only one scenario is active at a time per seed: the per-turn route
    blocks new triggers while one is in flight, and resolution flips the
    status to 'resolved' / 'aborted' so this query stops returning it.
    """
    return (
        db_session.query(Scenario)
        .filter(Scenario.seed_id == seed_id, Scenario.status == 'active')
        .order_by(Scenario.id.desc())
        .first()
    )


def load_state(scenario):
    """Decode the scenario's JSON state column into a dict (empty on miss)."""
    if not scenario or not scenario.state:
        return {}
    try:
        data = json.loads(scenario.state)
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_state(db_session, scenario, state):
    """Persist ``state`` back onto the scenario and bump ``updated_at``."""
    scenario.state = json.dumps(state or {})
    scenario.updated_at = datetime.datetime.now()
    db_session.add(scenario)
    db_session.flush()


def add_participant(db_session, scenario, character_id, role, order_index=0):
    """Enrol ``character_id`` in ``scenario`` with ``role``."""
    p = ScenarioParticipant(
        scenario_id=scenario.id,
        character_id=character_id,
        role=role,
        order_index=order_index,
    )
    db_session.add(p)
    db_session.flush()
    return p


def participants_by_role(db_session, scenario):
    """Return {role: [Character, ...]} ordered by ``order_index`` then id."""
    rows = (
        db_session.query(ScenarioParticipant)
        .filter(ScenarioParticipant.scenario_id == scenario.id)
        .order_by(ScenarioParticipant.order_index, ScenarioParticipant.id)
        .all()
    )
    out = {}
    for row in rows:
        out.setdefault(row.role, []).append(row.character)
    return out


def resolve(db_session, scenario, status, summary, *, current_turn=None):
    """Close ``scenario`` with ``status`` ('resolved' / 'aborted') + summary."""
    scenario.status = status
    scenario.summary = summary or ''
    scenario.turn_ended = current_turn
    scenario.resolved_at = datetime.datetime.now()
    scenario.updated_at = scenario.resolved_at
    db_session.add(scenario)
    db_session.flush()


def scenario_view(db_session, scenario):
    """Hand ``scenario`` to the matching handler's ``to_view`` for rendering."""
    from . import get_handler
    handler = get_handler(scenario.kind)
    if handler is None:
        return None
    return handler.to_view(db_session, scenario)


def lookup_characters_by_name(db_session, seed_id, names):
    """Resolve a list of character names to Character rows (case-insensitive).

    Names that don't match any row in the seed are dropped silently; the
    handler can decide whether the remaining list is enough to start the
    scenario or whether it should bail out.
    """
    cleaned = [(n or '').strip() for n in (names or []) if (n or '').strip()]
    if not cleaned:
        return []
    lowered = {n.lower() for n in cleaned}
    rows = (
        db_session.query(Character)
        .filter(Character.seed_id == seed_id)
        .all()
    )
    by_name = {(c.name or '').lower(): c for c in rows if c.name}
    out = []
    for name in cleaned:
        c = by_name.get(name.lower())
        if c is not None and c not in out:
            out.append(c)
    # Also catch first-name aliases if nothing matched.
    if not out:
        first_lookup = {}
        for c in rows:
            if not c.name:
                continue
            first = c.name.split()[0].lower()
            first_lookup.setdefault(first, c)
        for name in cleaned:
            c = first_lookup.get(name.split()[0].lower() if name else '')
            if c is not None and c not in out:
                out.append(c)
    _ = lowered  # silence linter; kept for future per-name validation
    return out


# --- Handler contract ------------------------------------------------------

class ScenarioHandler(ABC):
    """Contract every scenario kind implements.

    The substrate calls ``start`` once on transition, ``apply_action`` for
    every player input while the scenario is active, and ``to_view`` to
    render the read-only payload sent to the frontend.
    """

    kind = ''

    @abstractmethod
    def start(self, db_session, seed_id, trigger, *, current_turn=None,
              session_factory=None):
        """Create the Scenario row + participants for ``trigger``.

        Returns the freshly persisted ``Scenario``, or ``None`` when the
        trigger is unusable (e.g. no resolvable participants). Implementations
        commit through the supplied session.
        """

    @abstractmethod
    def apply_action(self, db_session, scenario, action, *,
                     gpt_service=None, session_factory=None,
                     current_turn=None):
        """Apply a player action to ``scenario`` and return a result payload.

        ``action`` is the JSON dict the frontend POSTed
        (``{"verb": "...", ...}``). The return shape is ``{"view": ...,
        "entries": [...], "resolved": bool, "summary": "..."}``; ``entries``
        are transcript items already persisted by the handler that the
        frontend should append to the narrative panel.
        """

    @abstractmethod
    def to_view(self, db_session, scenario):
        """Return the read-only payload describing the scenario's current state."""
