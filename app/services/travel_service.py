"""Overland travel helpers: distance, terrain cost, reachability.

Locations carry a (longitude, latitude) pair in arbitrary 'world units'
generated at world-build time and an undirected ``LocationConnection``
graph between top-level settlements. This module turns those two into a
deterministic time cost so the player's "go to X" intent can advance
the world clock without an LLM round-trip.

Sub-locations cluster inside a parent settlement; moving between them
is treated as a short stroll (``INTRA_SETTLEMENT_MINUTES``) regardless
of their lon/lat distance, matching how the world-build prompt
positions sub-locations near their parent's coordinates.

Distances are Euclidean across the lon/lat plane multiplied by
``UNITS_PER_KM`` so a step of 0.1 in world units is roughly 1 km. The
constant is loose on purpose -- the LLM is not consistent enough about
scale for higher-fidelity math to be worth it, and the travel prompt
already gives the player a "feels right" estimate.
"""
from __future__ import annotations

import math

from app.orm import Character, Location, LocationConnection
from app.services import time_service


# 1 lon/lat unit ~= 100 km. Tunable from a single place if the world
# build ever standardises on a different scale.
UNITS_PER_KM = 0.01

# Time cost (minutes) to walk between two sub-locations sharing a parent.
# A short in-settlement stroll; the arbiter can override per-action.
INTRA_SETTLEMENT_MINUTES = 10

# Multiplier on the base walking pace per terrain type. Anything missing
# from the table falls back to 1.0 (default ~5 km/h walking pace).
TERRAIN_SPEED_MULTIPLIER = {
    'road': 0.7, 'plains': 0.9, 'grassland': 0.9, 'farmland': 0.9,
    'desert': 1.4, 'sand': 1.4, 'forest': 1.3, 'jungle': 1.7,
    'swamp': 1.8, 'marsh': 1.8, 'hills': 1.3, 'mountains': 2.2,
    'snow': 1.6, 'tundra': 1.5, 'water': 2.5, 'coast': 1.1,
}


def _distance_units(a, b):
    """Euclidean distance in lon/lat units (None-safe)."""
    if a is None or b is None:
        return 0.0
    ax, ay = a.longitude or 0.0, a.latitude or 0.0
    bx, by = b.longitude or 0.0, b.latitude or 0.0
    return math.hypot(ax - bx, ay - by)


def distance_km(a, b):
    """Approximate distance between two locations in kilometres."""
    return _distance_units(a, b) / UNITS_PER_KM if UNITS_PER_KM else 0.0


def terrain_multiplier(terrain):
    """Per-terrain pace multiplier; >=1 slows travel, <1 speeds it up."""
    if not terrain:
        return 1.0
    return TERRAIN_SPEED_MULTIPLIER.get(str(terrain).strip().lower(), 1.0)


def travel_minutes(from_loc, to_loc):
    """Time cost (minutes) of moving from ``from_loc`` to ``to_loc``.

    Sub-locations inside the same parent settlement collapse to a flat
    ``INTRA_SETTLEMENT_MINUTES``. Otherwise the cost scales with the
    Euclidean distance and the worse of the two endpoints' terrain
    multipliers (the slower stretch dominates a journey).
    """
    if from_loc is None or to_loc is None or from_loc.id == to_loc.id:
        return 0
    if (from_loc.parent_id and to_loc.parent_id
            and from_loc.parent_id == to_loc.parent_id):
        return INTRA_SETTLEMENT_MINUTES
    km = distance_km(from_loc, to_loc)
    mult = max(terrain_multiplier(from_loc.terrain),
               terrain_multiplier(to_loc.terrain))
    minutes = int(round(km * time_service.DEFAULT_TRAVEL_MINUTES_PER_KM * mult))
    return max(INTRA_SETTLEMENT_MINUTES, minutes)


def reachable_destinations(db_session, seed_id, from_loc):
    """Return the locations the character can travel to from ``from_loc``.

    Three buckets, in this order:
      * sibling sub-locations (same parent settlement),
      * the parent settlement itself when ``from_loc`` is a sub-location,
      * top-level settlements connected by a ``LocationConnection``.

    Each entry is a dict with ``id``, ``name``, ``type``, ``terrain`` and
    ``minutes`` (the deterministic travel cost). Sorted by minutes so the
    UI can render a near-to-far list without further work.
    """
    if from_loc is None:
        return []
    out = {}

    if from_loc.parent_id is not None:
        siblings = (
            db_session.query(Location)
            .filter(Location.seed_id == seed_id,
                    Location.parent_id == from_loc.parent_id,
                    Location.id != from_loc.id)
            .all()
        )
        for loc in siblings:
            out[loc.id] = (loc, travel_minutes(from_loc, loc))
        parent = (
            db_session.query(Location)
            .filter(Location.id == from_loc.parent_id).first()
        )
        if parent is not None:
            out[parent.id] = (parent, INTRA_SETTLEMENT_MINUTES)
    else:
        # Standing at a top-level settlement -- the player can step into
        # any of its sub-locations (shops, taverns, gates, etc.) for the
        # flat in-settlement stroll cost.
        children = (
            db_session.query(Location)
            .filter(Location.seed_id == seed_id,
                    Location.parent_id == from_loc.id)
            .all()
        )
        for loc in children:
            out[loc.id] = (loc, INTRA_SETTLEMENT_MINUTES)

    anchor_id = from_loc.parent_id or from_loc.id
    edges = (
        db_session.query(LocationConnection)
        .filter(LocationConnection.seed_id == seed_id)
        .filter((LocationConnection.from_location_id == anchor_id) |
                (LocationConnection.to_location_id == anchor_id))
        .all()
    )
    for edge in edges:
        other_id = (edge.to_location_id if edge.from_location_id == anchor_id
                    else edge.from_location_id)
        if other_id == from_loc.id:
            continue
        loc = db_session.query(Location).filter(Location.id == other_id).first()
        if loc is None:
            continue
        out[loc.id] = (loc, travel_minutes(from_loc, loc))

    return [
        {'id': loc.id, 'name': loc.name, 'type': loc.type,
         'terrain': loc.terrain, 'minutes': minutes}
        for loc, minutes in sorted(out.values(), key=lambda t: t[1])
    ]


def resolve_current_location(db_session, seed_id, character):
    """Return the character's current Location row, falling back to the
    seed's first top-level location for legacy rows with NULL ``current_location_id``.
    """
    if character is None:
        return None
    if character.current_location_id:
        loc = (db_session.query(Location)
               .filter(Location.id == character.current_location_id).first())
        if loc is not None:
            return loc
    return (db_session.query(Location)
            .filter(Location.seed_id == seed_id,
                    Location.parent_id.is_(None))
            .order_by(Location.id.asc()).first())
