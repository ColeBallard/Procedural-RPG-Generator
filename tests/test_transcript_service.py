import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.orm import Base, Seed, TranscriptEntry
from app.routes import main as main_blueprint
from app.services import transcript_service


@pytest.fixture
def factory():
    engine = create_engine(
        'sqlite:///:memory:',
        connect_args={'check_same_thread': False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine)
    s = sf()
    s.add(Seed(id=1, current_turn=1, created_at=datetime.now(), updated_at=datetime.now()))
    s.commit()
    s.close()
    return sf


def test_add_entry_creates_row(factory):
    entry = transcript_service.add_entry(
        factory, 1, transcript_service.KIND_WORLD_BUILDING, 'hello world',
    )
    assert entry is not None
    assert entry.id is not None

    s = factory()
    rows = s.query(TranscriptEntry).all()
    s.close()
    assert len(rows) == 1
    assert rows[0].text == 'hello world'
    assert rows[0].kind == 'world_building'
    assert rows[0].meta is None


def test_add_entry_serialises_status_into_meta(factory):
    transcript_service.add_entry(
        factory, 1, transcript_service.KIND_WORLD_BUILDING, 'boom', status='error',
    )
    s = factory()
    row = s.query(TranscriptEntry).one()
    s.close()
    assert json.loads(row.meta) == {'status': 'error'}


def test_add_entry_swallows_errors_and_returns_none():
    bad_factory = MagicMock(side_effect=RuntimeError('db down'))
    # Even when the factory itself blows up, callers must not see it.
    result = transcript_service.add_entry(bad_factory, 1, 'system', 'x')
    assert result is None


def test_list_for_seed_returns_entries_in_insertion_order(factory):
    for text in ('first', 'second', 'third'):
        transcript_service.add_entry(
            factory, 1, transcript_service.KIND_WORLD_BUILDING, text,
        )
    s = factory()
    out = transcript_service.list_for_seed(s, 1)
    s.close()
    assert [e['text'] for e in out] == ['first', 'second', 'third']
    assert all(e['kind'] == 'world_building' for e in out)


def test_list_for_seed_isolates_by_seed(factory):
    s = factory()
    s.add(Seed(id=2, current_turn=1, created_at=datetime.now(), updated_at=datetime.now()))
    s.commit()
    s.close()
    transcript_service.add_entry(factory, 1, 'system', 'one')
    transcript_service.add_entry(factory, 2, 'system', 'two')
    s = factory()
    out = transcript_service.list_for_seed(s, 2)
    s.close()
    assert [e['text'] for e in out] == ['two']


def _make_app(factory):
    app = Flask(__name__)
    app.config['SESSION_FACTORY'] = factory
    app.config['min_grok'] = 'mock-model'
    app.register_blueprint(main_blueprint)
    return app


def test_get_world_includes_transcript(factory):
    transcript_service.add_entry(factory, 1, 'world_building', 'creating locations')
    transcript_service.add_entry(factory, 1, 'narration', 'You awaken at dawn.', speaker='Narrator')

    client = _make_app(factory).test_client()
    payload = client.get('/api/world/1').get_json()

    assert 'transcript' in payload
    assert [e['text'] for e in payload['transcript']] == [
        'creating locations', 'You awaken at dawn.',
    ]
    assert payload['transcript'][1]['speaker'] == 'Narrator'
    assert payload['transcript'][1]['kind'] == 'narration'


@patch('app.routes.OpenAI')
@patch('app.routes.WorldBuilder')
def test_stream_persists_progress_messages(mock_wb_cls, mock_openai_cls, factory):
    # Make the mocked WorldBuilder fire two progress callbacks before returning.
    def fake_build(self):
        self._captured_callback('locations done', 'info')
        self._captured_callback('characters done', 'info')
        return {}

    def init(self_, *args, **kwargs):
        self_._captured_callback = kwargs.get('progress_callback') or args[-1]

    instance = MagicMock()
    instance.build_world.side_effect = lambda: fake_build(instance)
    mock_wb_cls.side_effect = lambda *a, **kw: (init(instance, *a, **kw) or instance)

    client = _make_app(factory).test_client()
    response = client.post(
        '/initialize_world_building_stream',
        data=json.dumps({'seed_id': 1, 'seed_data': '{}', 'grok_api_key': 'k'}),
        content_type='application/json',
    )
    response.get_data()  # drain so background thread completes

    s = factory()
    rows = s.query(TranscriptEntry).order_by(TranscriptEntry.id).all()
    texts = [r.text for r in rows]
    s.close()
    assert 'locations done' in texts
    assert 'characters done' in texts
    assert all(r.kind == 'world_building' for r in rows)


@patch('app.routes.OpenAI')
@patch('app.routes.WorldBuilder')
def test_stream_persists_intro_narration_as_narration_entry(mock_wb_cls, mock_openai_cls, factory):
    # WorldBuilder returns intro_narration in its results dict; the route is
    # responsible for persisting it as a 'narration' transcript entry with
    # speaker 'Narrator' so it renders in the narrative panel after reload.
    intro_text = 'You wake to neon rain hammering the alley outside.'

    instance = MagicMock()
    instance.build_world.return_value = {'intro_narration': intro_text}
    mock_wb_cls.side_effect = lambda *a, **kw: instance

    client = _make_app(factory).test_client()
    response = client.post(
        '/initialize_world_building_stream',
        data=json.dumps({'seed_id': 1, 'seed_data': '{}', 'grok_api_key': 'k'}),
        content_type='application/json',
    )
    response.get_data()

    s = factory()
    rows = s.query(TranscriptEntry).filter_by(kind='narration').all()
    s.close()
    assert len(rows) == 1
    assert rows[0].text == intro_text
    assert rows[0].speaker == 'Narrator'


def test_world_builder_intro_narration_uses_gpt_response(factory):
    # _create_intro_narration delegates to GPTService.get_response and returns
    # the stripped text; this verifies the orchestration without an LLM.
    from app.world_building.world_building import WorldBuilder

    with patch('app.world_building.world_building.GPTService') as mock_gpt_cls, \
         patch('app.world_building.world_building.NameService'), \
         patch('app.world_building.world_building.CharacterBuilder'), \
         patch('app.world_building.world_building.LocationBuilder'):
        gpt = MagicMock()
        gpt.get_response.return_value = '  An opening passage.  '
        mock_gpt_cls.return_value = gpt

        wb = WorldBuilder({'theme': 'noir'}, 1, MagicMock(), MagicMock(), 'm')
        wb.character_builder.character_data = {'name': 'Mira', 'race': 'human'}
        wb.location_builder.locations = [
            {'id': 1, 'name': 'Old Pier', 'description': 'rotted wood'},
            {'id': 2, 'name': 'Market', 'description': 'busy stalls'},
        ]

        text = wb._create_intro_narration()

    assert text == 'An opening passage.'
    assert gpt.get_response.called
    prompt = gpt.get_response.call_args[0][0]
    assert 'Old Pier' in prompt
    assert 'Mira' in prompt


def test_world_builder_intro_narration_returns_none_without_locations(factory):
    from app.world_building.world_building import WorldBuilder

    with patch('app.world_building.world_building.GPTService'), \
         patch('app.world_building.world_building.NameService'), \
         patch('app.world_building.world_building.CharacterBuilder'), \
         patch('app.world_building.world_building.LocationBuilder'):
        wb = WorldBuilder({}, 1, MagicMock(), MagicMock(), 'm')
        wb.location_builder.locations = []
        assert wb._create_intro_narration() is None
