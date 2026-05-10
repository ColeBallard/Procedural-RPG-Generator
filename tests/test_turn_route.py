"""Tests for the per-turn narrative endpoints.

Covers ``POST /api/seed/<seed_id>/turn`` and ``GET /api/seed/<seed_id>/suggestions``.
The unit tests stub ``app.routes._make_gpt_service`` so the endpoints exercise
their own DB / transcript / context wiring without ever hitting the LLM. A
single ``@pytest.mark.llm`` smoke test still drives both endpoints against
the real Grok API to catch prompt-format regressions end-to-end.
"""
import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.orm import Base, Seed, Character, Location, TranscriptEntry
from app.routes import main as main_blueprint
from app.services import transcript_service


@pytest.fixture
def session_factory():
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


@pytest.fixture
def app(session_factory):
    flask_app = Flask(__name__)
    flask_app.config['SESSION_FACTORY'] = session_factory
    flask_app.config['min_grok'] = 'mock-model'
    flask_app.register_blueprint(main_blueprint)
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()


def _seed_ready_world(session_factory, seed_id=1, current_turn=1):
    """Insert a seed with a main character and a starting location.

    Mirrors the minimum the turn endpoint needs to build its LLM context.
    """
    s = session_factory()
    s.add(Seed(id=seed_id, current_turn=current_turn,
               created_at=datetime.now(), updated_at=datetime.now()))
    s.commit()
    s.add(Character(seed_id=seed_id, main_character=True, alive=True,
                    name='Hero', race='Human', gender=True, level=1,
                    current_health=100, max_health=100, current_currency=0))
    s.add(Location(seed_id=seed_id, name='Hamlet', description='A quiet hamlet',
                   type='village', climate='temperate', terrain='plains'))
    s.commit()
    s.close()


def _fake_gpt_service(narration='You step forward.', suggestions=None):
    """Return a MagicMock that mimics GPTService for both endpoints.

    ``get_response`` is called twice during a successful turn (narration,
    then suggestions); the side_effect list keeps the two responses
    distinct.
    """
    if suggestions is None:
        suggestions = ['Look around', 'Talk to a villager', 'Head north', 'Rest']
    svc = MagicMock()
    svc.get_response.side_effect = [
        narration,
        json.dumps({'suggestions': suggestions}),
    ]
    return svc


# ---- POST /api/seed/<seed_id>/turn ----


@patch('app.routes._make_gpt_service')
def test_submit_turn_persists_player_input_and_narration(mock_make, client, session_factory):
    _seed_ready_world(session_factory)
    mock_make.return_value = _fake_gpt_service(
        narration='The wind stirs the grass as you take your first step.',
        suggestions=['Walk to the well', 'Greet the blacksmith', 'Inspect the cart', 'Sit and listen'],
    )

    response = client.post('/api/seed/1/turn',
                           data=json.dumps({'action': 'Look around'}),
                           content_type='application/json')

    assert response.status_code == 200
    body = response.get_json()
    assert body['success'] is True
    assert 'wind stirs' in body['narration']
    assert body['suggestions'] == ['Walk to the well', 'Greet the blacksmith',
                                   'Inspect the cart', 'Sit and listen']
    assert body['turn'] == 2  # seed started at 1, bumped after success

    s = session_factory()
    rows = s.query(TranscriptEntry).order_by(TranscriptEntry.id).all()
    s.close()
    kinds = [r.kind for r in rows]
    assert kinds == [transcript_service.KIND_PLAYER_INPUT, transcript_service.KIND_NARRATION]
    assert rows[0].text == 'Look around'
    assert rows[0].speaker == 'You'
    assert rows[1].speaker == 'Narrator'


@patch('app.routes._make_gpt_service')
def test_submit_turn_bumps_current_turn(mock_make, client, session_factory):
    _seed_ready_world(session_factory, current_turn=5)
    mock_make.return_value = _fake_gpt_service()

    response = client.post('/api/seed/1/turn',
                           data=json.dumps({'action': 'Wait'}),
                           content_type='application/json')

    assert response.status_code == 200
    assert response.get_json()['turn'] == 6

    s = session_factory()
    seed = s.query(Seed).filter(Seed.id == 1).one()
    s.close()
    assert seed.current_turn == 6


def test_submit_turn_returns_400_on_empty_action(client, session_factory):
    _seed_ready_world(session_factory)
    response = client.post('/api/seed/1/turn',
                           data=json.dumps({'action': '   '}),
                           content_type='application/json')
    assert response.status_code == 400
    assert response.get_json()['success'] is False


def test_submit_turn_returns_404_on_unknown_seed(client):
    response = client.post('/api/seed/999/turn',
                           data=json.dumps({'action': 'Look around'}),
                           content_type='application/json')
    assert response.status_code == 404


def test_submit_turn_returns_409_when_world_not_ready(client, session_factory):
    # Seed exists but no main character / locations -> _build_turn_context
    # returns None, indicating world building hasn't completed yet.
    s = session_factory()
    s.add(Seed(id=7, current_turn=1, created_at=datetime.now(), updated_at=datetime.now()))
    s.commit()
    s.close()

    response = client.post('/api/seed/7/turn',
                           data=json.dumps({'action': 'Look around'}),
                           content_type='application/json')
    assert response.status_code == 409
    assert response.get_json()['success'] is False


@patch('app.routes._make_gpt_service')
def test_submit_turn_persists_player_input_even_when_narration_fails(
        mock_make, client, session_factory):
    _seed_ready_world(session_factory)
    svc = MagicMock()
    svc.get_response.side_effect = RuntimeError('LLM unavailable')
    mock_make.return_value = svc

    response = client.post('/api/seed/1/turn',
                           data=json.dumps({'action': 'Try a risky move'}),
                           content_type='application/json')

    assert response.status_code == 502
    assert response.get_json()['success'] is False

    s = session_factory()
    rows = s.query(TranscriptEntry).all()
    seed = s.query(Seed).filter(Seed.id == 1).one()
    s.close()
    # Player action must survive a failed narration so the user sees their
    # input echoed back on refresh; the turn counter must NOT advance.
    assert len(rows) == 1
    assert rows[0].kind == transcript_service.KIND_PLAYER_INPUT
    assert rows[0].text == 'Try a risky move'
    assert seed.current_turn == 1


# ---- GET /api/seed/<seed_id>/suggestions ----


@patch('app.routes._make_gpt_service')
def test_get_suggestions_returns_list(mock_make, client, session_factory):
    _seed_ready_world(session_factory)
    svc = MagicMock()
    svc.get_response.return_value = json.dumps({
        'suggestions': ['Open the door', 'Search the desk', 'Leave', 'Wait'],
    })
    mock_make.return_value = svc

    response = client.get('/api/seed/1/suggestions')
    assert response.status_code == 200
    assert response.get_json()['suggestions'] == [
        'Open the door', 'Search the desk', 'Leave', 'Wait',
    ]


def test_get_suggestions_returns_empty_when_world_not_ready(client, session_factory):
    s = session_factory()
    s.add(Seed(id=11, current_turn=1, created_at=datetime.now(), updated_at=datetime.now()))
    s.commit()
    s.close()

    response = client.get('/api/seed/11/suggestions')
    assert response.status_code == 200
    assert response.get_json()['suggestions'] == []


@patch('app.routes._make_gpt_service')
def test_get_suggestions_tolerates_llm_failure(mock_make, client, session_factory):
    # Suggestions are a UX nicety: a failing LLM call must degrade to an
    # empty list instead of surfacing an error to the player.
    _seed_ready_world(session_factory)
    svc = MagicMock()
    svc.get_response.side_effect = RuntimeError('boom')
    mock_make.return_value = svc

    response = client.get('/api/seed/1/suggestions')
    assert response.status_code == 200
    assert response.get_json()['suggestions'] == []


# ---- Live LLM smoke test ----


@pytest.mark.llm
@pytest.mark.slow
def test_turn_endpoint_drives_real_llm(grok_api_key, grok_model):
    """End-to-end: real Grok call returns narration + suggestions."""
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    _seed_ready_world(factory, seed_id=1)

    flask_app = Flask(__name__)
    flask_app.config['SESSION_FACTORY'] = factory
    flask_app.config['min_grok'] = grok_model
    flask_app.register_blueprint(main_blueprint)

    response = flask_app.test_client().post(
        '/api/seed/1/turn',
        data=json.dumps({'action': 'Look around the hamlet.'}),
        headers={'X-Grok-API-Key': grok_api_key},
        content_type='application/json',
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body['success'] is True
    assert isinstance(body['narration'], str) and len(body['narration']) > 0
    assert isinstance(body['suggestions'], list)
    assert body['turn'] == 2
