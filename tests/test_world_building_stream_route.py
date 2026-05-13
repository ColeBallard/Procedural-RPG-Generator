import json
from datetime import datetime

import pytest
from unittest.mock import MagicMock, patch
from flask import Flask
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.orm import Base, Seed
from app.routes import main as main_blueprint


def _parse_sse_events(body):
    events = []
    for chunk in body.split('\n\n'):
        chunk = chunk.strip()
        if not chunk:
            continue
        for line in chunk.split('\n'):
            if line.startswith('data: '):
                events.append(json.loads(line[len('data: '):]))
    return events


@pytest.fixture
def mock_app():
    """Flask app wired with mock session factory for failure-path tests."""
    flask_app = Flask(__name__)
    flask_app.config['SESSION_FACTORY'] = MagicMock(return_value=MagicMock())
    flask_app.config['min_grok'] = 'mock-model'
    flask_app.register_blueprint(main_blueprint)
    return flask_app


@pytest.fixture
def mock_client(mock_app):
    return mock_app.test_client()


@pytest.fixture
def live_app(grok_model):
    """Flask app wired with an in-memory SQLite session factory for real LLM tests.

    ``StaticPool`` + ``check_same_thread=False`` are required because the
    route spawns a background worker thread; without them each thread would
    receive its own empty in-memory database.
    """
    engine = create_engine(
        'sqlite:///:memory:',
        connect_args={'check_same_thread': False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)

    s = factory()
    for seed_id in (1, 2, 3):
        s.add(Seed(id=seed_id, current_turn=1,
                   created_at=datetime.now(), updated_at=datetime.now()))
    s.commit()
    s.close()

    flask_app = Flask(__name__)
    flask_app.config['SESSION_FACTORY'] = factory
    flask_app.config['min_grok'] = grok_model
    flask_app.register_blueprint(main_blueprint)
    return flask_app


@pytest.fixture
def live_client(live_app):
    return live_app.test_client()


@pytest.mark.llm
@pytest.mark.slow
def test_stream_emits_progress_and_complete_events(live_client, grok_api_key):
    response = live_client.post(
        '/initialize_world_building_stream',
        data=json.dumps({
            'seed_id': 1,
            'seed_data': '{"theme": "fantasy"}',
        }),
        headers={'X-Grok-API-Key': grok_api_key},
        content_type='application/json'
    )

    assert response.status_code == 200
    assert response.mimetype == 'text/event-stream'

    events = _parse_sse_events(response.get_data(as_text=True))

    progress_events = [e for e in events if e['type'] == 'progress']
    complete_events = [e for e in events if e['type'] == 'complete']
    error_events = [e for e in events if e['type'] == 'error']

    assert error_events == []
    assert len(progress_events) > 0
    assert any('main character' in e['message'].lower() for e in progress_events)
    assert any('complete' in e['message'].lower() for e in progress_events)

    assert len(complete_events) == 1
    results = complete_events[0]['results']
    assert results.get('main_character', {}).get('status') == 'success'
    assert results.get('locations', {}).get('status') == 'success'


@patch('app.routes.OpenAI')
@patch('app.routes.WorldBuilder')
def test_stream_emits_error_event_on_exception(mock_world_builder_cls, mock_openai_cls, mock_client):
    instance = MagicMock()
    instance.build_world.side_effect = RuntimeError("boom")
    mock_world_builder_cls.return_value = instance

    response = mock_client.post(
        '/initialize_world_building_stream',
        data=json.dumps({
            'seed_id': 2,
            'seed_data': '{}',
        }),
        headers={'X-Grok-API-Key': 'test-key'},
        content_type='application/json'
    )

    assert response.status_code == 200
    events = _parse_sse_events(response.get_data(as_text=True))

    error_events = [e for e in events if e['type'] == 'error']
    assert len(error_events) == 1
    # The route deliberately returns a generic message (not str(e)) to
    # avoid leaking exception detail to the client.
    assert error_events[0]['message'] == "World building failed; please retry."


@patch('app.routes.OpenAI')
@patch('app.routes.WorldBuilder')
def test_stream_cleans_up_progress_queue(mock_world_builder_cls, mock_openai_cls, mock_client):
    from app.routes import progress_queues

    instance = MagicMock()
    instance.build_world.return_value = {}
    mock_world_builder_cls.return_value = instance

    initial_queue_count = len(progress_queues)

    response = mock_client.post(
        '/initialize_world_building_stream',
        data=json.dumps({
            'seed_id': 3,
            'seed_data': '{}',
        }),
        headers={'X-Grok-API-Key': 'test-key'},
        content_type='application/json'
    )
    # Drain the response so the generator's finally block runs.
    response.get_data()

    assert len(progress_queues) == initial_queue_count
