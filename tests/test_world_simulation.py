"""Tests for the autonomous off-screen events simulator.

The simulator runs at most every ``MIN_INTERVAL_MINUTES`` of in-world
time, drafts events via the LLM, and persists matching ones as ``Event``
rows. We stub the LLM so the tests stay deterministic.
"""
import datetime as dt
from unittest.mock import MagicMock

import pytest

from app.orm import Character, Event, EventCharacter, Location, Seed
from app.services import world_simulation as ws
from app.world_building.schemas import BackgroundEventOut, BackgroundEventsOut


@pytest.fixture
def world(db_session, session_factory):
    s = Seed(
        id=1, current_turn=2,
        current_date_time=dt.datetime(2025, 5, 12, 12, 0),
    )
    db_session.add(s)
    db_session.commit()
    here = Location(seed_id=1, name='Hamlet', type='village',
                    terrain='plains', longitude=0.0, latitude=0.0)
    away = Location(seed_id=1, name='Keep', type='fortress',
                   terrain='hills', longitude=0.1, latitude=0.0)
    db_session.add_all([here, away])
    db_session.flush()
    npc = Character(seed_id=1, main_character=False, alive=True,
                    name='Marek', level=1)
    db_session.add(npc)
    db_session.commit()
    return {'seed': s, 'here': here, 'away': away, 'npc': npc,
            'session_factory': session_factory}


def test_should_simulate_false_without_clock(db_session):
    s = Seed(id=1, current_turn=1)
    db_session.add(s); db_session.commit()
    assert ws.should_simulate(s) is False


def test_should_simulate_true_on_first_run(world):
    # last_event_sim_at is None -- the very first tick is always due.
    assert ws.should_simulate(world['seed']) is True


def test_should_simulate_respects_min_interval(world):
    seed = world['seed']
    seed.last_event_sim_at = seed.current_date_time - dt.timedelta(minutes=30)
    assert ws.should_simulate(seed) is False
    seed.last_event_sim_at = seed.current_date_time - dt.timedelta(
        minutes=ws.MIN_INTERVAL_MINUTES)
    assert ws.should_simulate(seed) is True


def test_maybe_simulate_skips_when_not_due(world, db_session):
    seed = world['seed']
    seed.last_event_sim_at = seed.current_date_time
    db_session.commit()
    gpt = MagicMock()
    events, entries = ws.maybe_simulate(
        db_session, 1, gpt, context={}, session_factory=world['session_factory'],
    )
    assert events == [] and entries == []
    gpt.get_structured.assert_not_called()


def test_maybe_simulate_persists_matched_event(world, db_session):
    gpt = MagicMock()
    gpt.get_structured.return_value = BackgroundEventsOut(events=[
        BackgroundEventOut(name='Bandit Raid', description='Smoke on the horizon.',
                            type='incident', location_name='Keep',
                            participant_names=['Marek']),
    ])
    ctx = {k: '' for k in ('seed_data', 'character', 'starting_location',
                            'other_locations', 'existing_characters',
                            'transcript', 'world_clock')}
    events, entries = ws.maybe_simulate(
        db_session, 1, gpt, context=ctx,
        session_factory=world['session_factory'],
    )
    assert len(events) == 1
    ev = events[0]
    assert ev.name == 'Bandit Raid'
    assert ev.location_id == world['away'].id
    # Participant matched and recorded.
    ec = (db_session.query(EventCharacter)
          .filter(EventCharacter.event_id == ev.id).all())
    assert len(ec) == 1 and ec[0].character_id == world['npc'].id
    # last_event_sim_at advances so the next call won't refire immediately.
    assert world['seed'].last_event_sim_at == world['seed'].current_date_time
    # A transcript entry was emitted for the news.
    assert len(entries) == 1
    assert entries[0]['kind'] == 'system'
    assert 'Bandit Raid' in entries[0]['text']


def _full_ctx():
    """All slots required by the BACKGROUND_EVENTS template."""
    return {k: '' for k in ('seed_data', 'character', 'starting_location',
                            'other_locations', 'existing_characters',
                            'transcript', 'world_clock')}


def test_maybe_simulate_drops_unmatched_locations(world, db_session):
    gpt = MagicMock()
    gpt.get_structured.return_value = BackgroundEventsOut(events=[
        BackgroundEventOut(name='Ghost Sighting', location_name='Atlantis'),
    ])
    events, entries = ws.maybe_simulate(
        db_session, 1, gpt, context=_full_ctx(),
        session_factory=world['session_factory'],
    )
    assert events == [] and entries == []
    # Even on a no-match payload the cooldown still advances.
    assert world['seed'].last_event_sim_at == world['seed'].current_date_time


def test_maybe_simulate_swallows_llm_failure(world, db_session):
    gpt = MagicMock()
    gpt.get_structured.side_effect = RuntimeError('boom')
    events, entries = ws.maybe_simulate(
        db_session, 1, gpt, context=_full_ctx(),
        session_factory=world['session_factory'],
    )
    assert events == [] and entries == []
    # Cooldown still bumps so a wedged model doesn't retry every turn.
    assert world['seed'].last_event_sim_at == world['seed'].current_date_time


def test_maybe_simulate_caps_event_count(world, db_session):
    drafts = [
        BackgroundEventOut(name=f'E{i}', location_name='Keep')
        for i in range(5)
    ]
    gpt = MagicMock()
    gpt.get_structured.return_value = BackgroundEventsOut(events=drafts)
    events, _ = ws.maybe_simulate(
        db_session, 1, gpt, context=_full_ctx(),
        session_factory=world['session_factory'],
    )
    assert len(events) == ws.MAX_EVENTS_PER_TICK


def test_recent_offscreen_filters_current_location(db_session, world):
    seed = world['seed']
    db_session.add_all([
        Event(seed_id=1, name='Old1', location_id=world['here'].id,
              start_date_time=seed.current_date_time - dt.timedelta(hours=1),
              start_turn=1, type='incident'),
        Event(seed_id=1, name='Old2', location_id=world['away'].id,
              start_date_time=seed.current_date_time - dt.timedelta(hours=2),
              start_turn=1, type='incident'),
    ])
    db_session.commit()
    out = ws.recent_offscreen_events(db_session, 1, world['here'].id, limit=4)
    names = [e.name for e in out]
    assert 'Old2' in names and 'Old1' not in names


def test_recent_offscreen_no_anchor_returns_all(db_session, world):
    seed = world['seed']
    db_session.add(Event(seed_id=1, name='Anywhere',
                         location_id=world['away'].id,
                         start_date_time=seed.current_date_time,
                         start_turn=1, type='incident'))
    db_session.commit()
    out = ws.recent_offscreen_events(db_session, 1, None, limit=4)
    assert any(e.name == 'Anywhere' for e in out)
