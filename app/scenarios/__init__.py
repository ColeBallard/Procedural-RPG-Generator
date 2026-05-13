"""Scenario substrate: structured mid-turn interactions.

A ``Scenario`` is a typed interaction (battle, dialogue, trade, ...) that
temporarily replaces the free-form narrator turn loop with a constrained
action vocabulary. The narrator LLM signals a transition via the optional
``scenario_trigger`` field on ``TurnResponseOut``; the substrate persists a
``Scenario`` row, enrols participants, and from then on routes the player's
input through the matching handler in this package until the scenario
resolves (one side wins, both walk away, the player aborts, ...).

Each handler module exposes a ``handler`` instance subclassed from
``ScenarioHandler``; ``HANDLERS`` maps the kind string to that instance so
``app.routes`` can dispatch without importing every module by name.
"""
from __future__ import annotations

from .base import (
    KIND_DIALOGUE,
    KIND_BATTLE,
    KIND_TRADE,
    ScenarioHandler,
    active_scenario_for,
    scenario_view,
)
from .dialogue import handler as dialogue_handler
from .battle import handler as battle_handler
from .trade import handler as trade_handler


HANDLERS = {
    KIND_DIALOGUE: dialogue_handler,
    KIND_BATTLE: battle_handler,
    KIND_TRADE: trade_handler,
}


def get_handler(kind):
    """Return the registered handler for ``kind`` or ``None`` if unknown."""
    return HANDLERS.get((kind or '').strip().lower())


__all__ = [
    'HANDLERS',
    'KIND_DIALOGUE',
    'KIND_BATTLE',
    'KIND_TRADE',
    'ScenarioHandler',
    'active_scenario_for',
    'scenario_view',
    'get_handler',
]
