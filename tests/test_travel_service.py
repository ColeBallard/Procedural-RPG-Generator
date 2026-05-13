"""Tests for the overland travel helpers.

The travel service turns a (longitude, latitude) graph plus terrain into
deterministic minute costs and a "what can I reach from here" listing
that the /travel endpoint hands to the frontend panel.
"""
import pytest

from app.orm import Character, Location, LocationConnection, Seed
from app.services import travel_service as tr


@pytest.fixture
def seeded_world(db_session):
    """A small world: two settlements connected by a road, sub-locations
    inside the first, and a player character anchored at one sub-location.
    """
    s = Seed(id=1, current_turn=1)
    db_session.add(s)
    db_session.commit()

    # Two top-level settlements, ~10 km apart on a road.
    hamlet = Location(seed_id=1, name='Hamlet', type='village',
                      terrain='plains', longitude=0.0, latitude=0.0)
    keep = Location(seed_id=1, name='Keep', type='fortress',
                    terrain='hills', longitude=0.1, latitude=0.0)
    db_session.add_all([hamlet, keep])
    db_session.flush()

    # Two sub-locations inside Hamlet (sibling rows under the same parent).
    tavern = Location(seed_id=1, name='Tavern', type='tavern',
                     terrain='plains', longitude=0.0, latitude=0.0,
                     parent_id=hamlet.id)
    smithy = Location(seed_id=1, name='Smithy', type='smithy',
                     terrain='plains', longitude=0.0, latitude=0.0,
                     parent_id=hamlet.id)
    db_session.add_all([tavern, smithy])
    db_session.flush()

    db_session.add(LocationConnection(
        seed_id=1, from_location_id=hamlet.id, to_location_id=keep.id,
        name='Old Road', type='road',
    ))
    db_session.add(Character(
        seed_id=1, main_character=True, alive=True, name='Hero', level=1,
        current_location_id=tavern.id,
    ))
    db_session.commit()
    return {
        'seed': s, 'hamlet': hamlet, 'keep': keep,
        'tavern': tavern, 'smithy': smithy,
    }


def test_distance_km_is_euclidean(seeded_world):
    km = tr.distance_km(seeded_world['hamlet'], seeded_world['keep'])
    # 0.1 lon-units / 0.01 units-per-km = 10 km.
    assert km == pytest.approx(10.0, rel=1e-6)


def test_distance_km_handles_missing_endpoint():
    assert tr.distance_km(None, None) == 0.0


def test_terrain_multiplier_known_and_unknown():
    assert tr.terrain_multiplier('road') < 1.0
    assert tr.terrain_multiplier('mountains') > 1.0
    assert tr.terrain_multiplier('not_a_real_terrain') == 1.0
    assert tr.terrain_multiplier(None) == 1.0
    assert tr.terrain_multiplier('  Plains  ') == tr.TERRAIN_SPEED_MULTIPLIER['plains']


def test_travel_minutes_self_is_zero(seeded_world):
    assert tr.travel_minutes(seeded_world['hamlet'], seeded_world['hamlet']) == 0


def test_travel_minutes_intra_settlement_flat(seeded_world):
    minutes = tr.travel_minutes(seeded_world['tavern'], seeded_world['smithy'])
    assert minutes == tr.INTRA_SETTLEMENT_MINUTES


def test_travel_minutes_inter_settlement_uses_terrain(seeded_world):
    # Hamlet plains (0.9) vs Keep hills (1.3) -- the worse multiplier wins.
    minutes = tr.travel_minutes(seeded_world['hamlet'], seeded_world['keep'])
    expected = round(10.0 * 12 * 1.3)  # km * default minutes/km * hills mult
    assert minutes == expected


def test_travel_minutes_handles_none_endpoints():
    assert tr.travel_minutes(None, None) == 0


def test_reachable_from_sub_location_lists_siblings_and_parent(db_session, seeded_world):
    dests = tr.reachable_destinations(db_session, 1, seeded_world['tavern'])
    names = {d['name'] for d in dests}
    # Sibling sub-locations + the parent settlement, plus settlements
    # reachable via the connection on the parent's anchor.
    assert 'Smithy' in names
    assert 'Hamlet' in names
    assert 'Keep' in names
    # Sorted by minutes ascending.
    minutes = [d['minutes'] for d in dests]
    assert minutes == sorted(minutes)


def test_reachable_from_top_level_lists_children_and_neighbours(db_session, seeded_world):
    dests = tr.reachable_destinations(db_session, 1, seeded_world['hamlet'])
    names = {d['name'] for d in dests}
    assert 'Tavern' in names and 'Smithy' in names  # children
    assert 'Keep' in names                          # connected settlement
    assert 'Hamlet' not in names                    # never lists self


def test_reachable_handles_no_anchor():
    assert tr.reachable_destinations(None, 1, None) == []


def test_resolve_current_location_uses_character_pointer(db_session, seeded_world):
    char = (db_session.query(Character)
            .filter(Character.seed_id == 1).first())
    loc = tr.resolve_current_location(db_session, 1, char)
    assert loc.id == seeded_world['tavern'].id


def test_resolve_current_location_falls_back_to_first_top_level(db_session, seeded_world):
    char = (db_session.query(Character)
            .filter(Character.seed_id == 1).first())
    char.current_location_id = None
    db_session.commit()
    loc = tr.resolve_current_location(db_session, 1, char)
    # Falls back to the first top-level location for the seed.
    assert loc.parent_id is None
    assert loc.id == seeded_world['hamlet'].id


def test_resolve_current_location_handles_missing_character(db_session):
    assert tr.resolve_current_location(db_session, 1, None) is None
