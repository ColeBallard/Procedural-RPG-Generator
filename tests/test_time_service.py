"""Tests for the in-world clock helpers.

The world clock lives on ``Seed.current_date_time``; this module is the
only sanctioned writer. Reads are pure helpers that the prompt context
and frontend payload share.
"""
import datetime as dt

import pytest

from app.orm import Seed
from app.services import time_service as t


@pytest.fixture
def seed_with_clock(db_session):
    s = Seed(
        id=1, current_turn=1,
        created_at=dt.datetime(2025, 5, 12, 10, 0),
        updated_at=dt.datetime(2025, 5, 12, 10, 0),
        current_date_time=dt.datetime(2025, 5, 12, 10, 0),
    )
    db_session.add(s)
    db_session.commit()
    return s


def test_now_world_returns_seed_clock(seed_with_clock):
    assert t.now_world(seed_with_clock) == dt.datetime(2025, 5, 12, 10, 0)


def test_now_world_handles_missing_seed():
    assert t.now_world(None) is None


def test_advance_time_moves_clock_forward(db_session, seed_with_clock):
    new = t.advance_time(db_session, seed_with_clock, 30)
    assert new == dt.datetime(2025, 5, 12, 10, 30)
    assert seed_with_clock.current_date_time == new


def test_advance_time_clamps_negative_to_zero(db_session, seed_with_clock):
    before = seed_with_clock.current_date_time
    new = t.advance_time(db_session, seed_with_clock, -120)
    assert new == before  # zero-delta no-op, returns the same datetime


def test_advance_time_caps_extreme_values(db_session, seed_with_clock):
    # An LLM hallucinating "skip 30 days" must clamp to a week.
    new = t.advance_time(db_session, seed_with_clock, 60 * 24 * 365)
    assert new == seed_with_clock.current_date_time
    delta = new - dt.datetime(2025, 5, 12, 10, 0)
    assert delta == dt.timedelta(minutes=t.MAX_TURN_MINUTES)


def test_advance_time_noop_on_seed_without_clock(db_session):
    s = Seed(id=2, current_turn=1)
    db_session.add(s)
    db_session.commit()
    # No clock yet -- helper returns None and does not blow up.
    assert t.advance_time(db_session, s, 60) is None
    assert s.current_date_time is None


def test_advance_time_handles_none_minutes(db_session, seed_with_clock):
    before = seed_with_clock.current_date_time
    assert t.advance_time(db_session, seed_with_clock, None) == before


def test_format_clock_includes_time_of_day():
    out = t.format_clock(dt.datetime(2025, 5, 12, 17, 30))
    assert '2025-05-12 17:30' in out
    assert 'afternoon' in out


def test_format_clock_handles_none():
    assert t.format_clock(None) == ''


@pytest.mark.parametrize('hour, label', [
    (3, 'late night'),
    (6, 'dawn'),
    (10, 'morning'),
    (13, 'noon'),
    (16, 'afternoon'),
    (19, 'evening'),
    (22, 'night'),
])
def test_time_of_day_buckets(hour, label):
    assert t.time_of_day(dt.datetime(2025, 5, 12, hour, 0)) == label


def test_time_of_day_handles_none():
    assert t.time_of_day(None) == 'unknown'


def test_serialize_clock_payload_keys(seed_with_clock):
    payload = t.serialize_clock(seed_with_clock)
    assert payload['iso'] == '2025-05-12T10:00'
    assert payload['time_of_day'] == 'morning'
    assert payload['minute_of_day'] == 10 * 60
    assert payload['day_of_year'] == dt.datetime(2025, 5, 12).timetuple().tm_yday
    assert '2025-05-12 10:00' in payload['display']


def test_serialize_clock_handles_unset_seed(db_session):
    s = Seed(id=3, current_turn=1)
    db_session.add(s)
    db_session.commit()
    payload = t.serialize_clock(s)
    assert payload['iso'] is None
    assert payload['display'] == ''
    assert payload['time_of_day'] == 'unknown'
    assert payload['minute_of_day'] is None
    assert payload['day_of_year'] is None
