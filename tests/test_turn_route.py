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

from app.orm import (
    Base, Seed, Character, CharacterRelationship, Location, TranscriptEntry,
)
from app.routes import main as main_blueprint
from app.services import transcript_service
from app.world_building.schemas import (
    ActionAdjudicationOut, BackgroundEventsOut,
    TurnDialogueLineOut, TurnNewCharacterOut, TurnResponseOut,
)


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


def _fake_gpt_service(narration='You step forward.', suggestions=None,
                      dialogue=None, new_characters=None,
                      ruling=None):
    """Return a MagicMock that mimics GPTService for both endpoints.

    ``get_structured`` is called multiple times per turn -- the arbiter
    adjudication pass, the narration pass, and (when the in-world clock
    has advanced enough) the autonomous-events pass. The mock dispatches
    by schema so each call gets a payload of the correct shape.
    ``get_response`` is called once afterwards for the suggestions JSON.
    """
    if suggestions is None:
        suggestions = ['Look around', 'Talk to a villager', 'Head north', 'Rest']
    if ruling is None:
        # Auto-success ruling so legacy tests that don't care about dice
        # behave exactly as they did before Phase 3 was wired in.
        ruling = ActionAdjudicationOut(
            requires_check=False, ability='strength', dc=10,
            proficient=False, advantage=False, disadvantage=False,
            time_cost_minutes=5, reason='',
        )
    turn_payload = TurnResponseOut(
        narration=narration,
        dialogue=list(dialogue or []),
        new_characters=list(new_characters or []),
    )

    def _dispatch(prompt, schema, *args, **kwargs):
        if schema is ActionAdjudicationOut:
            return ruling
        if schema is BackgroundEventsOut:
            return BackgroundEventsOut(events=[])
        return turn_payload

    svc = MagicMock()
    svc.get_structured.side_effect = _dispatch
    svc.get_response.return_value = json.dumps({'suggestions': suggestions})
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
    svc.get_structured.side_effect = RuntimeError('LLM unavailable')
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


@patch('app.routes._make_gpt_service')
def test_submit_turn_splits_narration_into_paragraph_entries(
        mock_make, client, session_factory):
    """A multi-paragraph narration must land as one entry per paragraph.

    Smaller entries let the TTS layer start playback on the first paragraph
    instead of waiting for the whole block to synthesize.
    """
    _seed_ready_world(session_factory)
    multi = (
        "The wind stirs the grass.\n\n"
        "A crow watches from the eaves, head cocked.\n\n"
        "Smoke threads up from the smithy down the lane."
    )
    mock_make.return_value = _fake_gpt_service(narration=multi)

    response = client.post('/api/seed/1/turn',
                           data=json.dumps({'action': 'Look around'}),
                           content_type='application/json')
    assert response.status_code == 200
    body = response.get_json()

    narration_entries = [e for e in body['entries']
                         if e['kind'] == transcript_service.KIND_NARRATION]
    assert len(narration_entries) == 3
    assert all(e['speaker'] == 'Narrator' for e in narration_entries)
    assert narration_entries[0]['text'] == 'The wind stirs the grass.'
    assert narration_entries[2]['text'].startswith('Smoke threads up')

    s = session_factory()
    rows = (s.query(TranscriptEntry)
            .filter(TranscriptEntry.kind == transcript_service.KIND_NARRATION)
            .order_by(TranscriptEntry.id).all())
    s.close()
    assert [r.text for r in rows] == [
        'The wind stirs the grass.',
        'A crow watches from the eaves, head cocked.',
        'Smoke threads up from the smithy down the lane.',
    ]


@patch('app.routes._make_gpt_service')
def test_submit_turn_persists_dialogue_with_speaker(mock_make, client, session_factory):
    """Dialogue lines must land as separate entries attributed to the speaker."""
    _seed_ready_world(session_factory)
    # Pre-existing NPC the LLM will quote so name resolution stays canonical.
    s = session_factory()
    s.add(Character(seed_id=1, main_character=False, alive=True, name='Mira',
                    race='Human', gender=False, level=1,
                    current_health=100, max_health=100, current_currency=0))
    s.commit()
    s.close()

    mock_make.return_value = _fake_gpt_service(
        narration='You step into the inn.',
        dialogue=[
            TurnDialogueLineOut(speaker='mira', text='Welcome, traveller.'),
        ],
    )

    response = client.post('/api/seed/1/turn',
                           data=json.dumps({'action': 'Talk to the innkeeper'}),
                           content_type='application/json')
    assert response.status_code == 200
    body = response.get_json()
    # Response carries the structured entries list so the frontend can render
    # the narration + dialogue in order with per-speaker styling.
    kinds = [e['kind'] for e in body['entries']]
    assert kinds == [transcript_service.KIND_NARRATION,
                     transcript_service.KIND_DIALOGUE]
    assert body['entries'][1]['speaker'] == 'Mira'
    assert body['entries'][1]['text'] == 'Welcome, traveller.'

    s = session_factory()
    rows = s.query(TranscriptEntry).order_by(TranscriptEntry.id).all()
    s.close()
    # Player input + narration + dialogue in that order.
    assert [r.kind for r in rows] == [
        transcript_service.KIND_PLAYER_INPUT,
        transcript_service.KIND_NARRATION,
        transcript_service.KIND_DIALOGUE,
    ]
    assert rows[2].speaker == 'Mira'


@patch('app.routes._make_gpt_service')
def test_submit_turn_skips_narration_entry_when_empty(
        mock_make, client, session_factory):
    """An empty narration must not produce a phantom 'Narrator' entry.

    The CONTINUE_NARRATIVE prompt makes narration optional so the model can
    stay silent on purely conversational beats. When that happens the turn
    should persist only the player input + dialogue line(s); no empty
    narration row, no narration item in the response ``entries`` list.
    """
    _seed_ready_world(session_factory)
    s = session_factory()
    s.add(Character(seed_id=1, main_character=False, alive=True, name='Mira',
                    race='Human', gender=False, level=1,
                    current_health=100, max_health=100, current_currency=0))
    s.commit()
    s.close()

    mock_make.return_value = _fake_gpt_service(
        narration='',
        dialogue=[TurnDialogueLineOut(speaker='Mira', text='Back again?')],
    )

    response = client.post('/api/seed/1/turn',
                           data=json.dumps({'action': 'Ask Mira about the weather'}),
                           content_type='application/json')
    assert response.status_code == 200
    body = response.get_json()
    assert body['narration'] == ''
    assert body['narration_id'] is None
    # Only the dialogue line ships back in entries; no narration placeholder.
    kinds = [e['kind'] for e in body['entries']]
    assert kinds == [transcript_service.KIND_DIALOGUE]
    assert body['entries'][0]['speaker'] == 'Mira'

    s = session_factory()
    rows = s.query(TranscriptEntry).order_by(TranscriptEntry.id).all()
    s.close()
    # Player input + dialogue only -- no narration row was persisted.
    assert [r.kind for r in rows] == [
        transcript_service.KIND_PLAYER_INPUT,
        transcript_service.KIND_DIALOGUE,
    ]


@patch('app.routes._make_gpt_service')
def test_submit_turn_creates_dynamic_character(mock_make, client, session_factory):
    """A character introduced mid-game is persisted with their dialogue."""
    _seed_ready_world(session_factory)

    new_char = TurnNewCharacterOut(
        name='Brann the Smith', race='Human', gender='male',
        date_of_birth='0985-04-12',
        description='gruff baritone with a faint northern lilt',
    )
    mock_make.return_value = _fake_gpt_service(
        narration='A burly figure looks up from the anvil.',
        dialogue=[TurnDialogueLineOut(speaker='Brann the Smith',
                                       text='Need something sharpened?')],
        new_characters=[new_char],
    )

    response = client.post('/api/seed/1/turn',
                           data=json.dumps({'action': 'Approach the smith'}),
                           content_type='application/json')
    assert response.status_code == 200
    body = response.get_json()
    assert any(c['name'] == 'Brann the Smith' for c in body['new_characters'])

    s = session_factory()
    chars = (s.query(Character)
             .filter(Character.seed_id == 1, Character.name == 'Brann the Smith')
             .all())
    rows = s.query(TranscriptEntry).order_by(TranscriptEntry.id).all()
    s.close()
    assert len(chars) == 1
    assert chars[0].main_character is False
    # Dialogue speaker matches the canonical character name.
    dialogue_rows = [r for r in rows if r.kind == transcript_service.KIND_DIALOGUE]
    assert dialogue_rows and dialogue_rows[0].speaker == 'Brann the Smith'


@patch('app.routes._extract_elevenlabs_api_key', return_value='el-key')
@patch('app.routes.elevenlabs_service.find_voice_for_character',
       return_value='voice-abc')
@patch('app.routes._make_gpt_service')
def test_submit_turn_assigns_voice_id_to_dynamic_character(
        mock_make, mock_find, mock_key, client, session_factory):
    """Voice search is invoked and its result lands on the new Character row.

    Without this the dynamic NPC would inherit the narrator's voice at TTS
    time because ``_resolve_voice_id_for_speaker`` falls back to the
    narrator id when ``Character.voice_id`` is NULL.
    """
    _seed_ready_world(session_factory)
    new_char = TurnNewCharacterOut(
        name='Edda the Innkeeper', race='Human', gender='female',
        date_of_birth='0980-06-22',
        description='warm contralto, motherly cadence',
    )
    mock_make.return_value = _fake_gpt_service(
        narration='The innkeeper looks up.',
        dialogue=[TurnDialogueLineOut(speaker='Edda the Innkeeper',
                                       text='Welcome, traveller.')],
        new_characters=[new_char],
    )

    response = client.post('/api/seed/1/turn',
                           data=json.dumps({'action': 'Greet the innkeeper'}),
                           content_type='application/json')
    assert response.status_code == 200

    # The voice search ran with the LLM-supplied traits forwarded straight
    # through; we don't pin every kwarg to keep the test resilient to
    # additional hints, just the ones the user-visible bug depended on.
    assert mock_find.called
    kwargs = mock_find.call_args.kwargs
    assert kwargs['gender'] is False
    assert kwargs['race'] == 'Human'
    assert kwargs['search_text'] == 'warm contralto, motherly cadence'

    s = session_factory()
    char = (s.query(Character)
            .filter(Character.seed_id == 1,
                    Character.name == 'Edda the Innkeeper')
            .first())
    s.close()
    assert char is not None
    assert char.voice_id == 'voice-abc'


@patch('app.routes._make_gpt_service')
def test_submit_turn_creates_mc_relationship_for_dynamic_character(
        mock_make, client, session_factory):
    """The MC must end the turn with an acquaintance row to the new NPC.

    Without this the new character is rendered as an "unknown" stranger by
    the world payload (familiarity == 0) and won't appear in the
    Characters accordion.
    """
    _seed_ready_world(session_factory)
    new_char = TurnNewCharacterOut(
        name='Brann the Smith', race='Human', gender='male',
    )
    mock_make.return_value = _fake_gpt_service(
        narration='A burly figure looks up from the anvil.',
        new_characters=[new_char],
    )

    response = client.post('/api/seed/1/turn',
                           data=json.dumps({'action': 'Approach the smith'}),
                           content_type='application/json')
    assert response.status_code == 200

    s = session_factory()
    mc = (s.query(Character)
          .filter(Character.seed_id == 1,
                  Character.main_character.is_(True))
          .first())
    npc = (s.query(Character)
           .filter(Character.seed_id == 1,
                   Character.name == 'Brann the Smith')
           .first())
    rel = (s.query(CharacterRelationship)
           .filter(CharacterRelationship.character_id == mc.id,
                   CharacterRelationship.related_character_id == npc.id)
           .first())
    s.close()
    assert rel is not None
    # familiarity must be >= 1 so the MC isn't read back as "unknown".
    assert rel.familiarity >= 1
    assert rel.relationship_type == 'acquaintance'


@patch('app.routes._make_gpt_service')
def test_submit_turn_skips_duplicate_new_character(mock_make, client, session_factory):
    """A character with a name already in the seed is not duplicated."""
    _seed_ready_world(session_factory)
    s = session_factory()
    s.add(Character(seed_id=1, main_character=False, alive=True, name='Mira',
                    race='Human', gender=False, level=1,
                    current_health=100, max_health=100, current_currency=0))
    s.commit()
    s.close()

    mock_make.return_value = _fake_gpt_service(
        narration='Mira nods at you.',
        dialogue=[TurnDialogueLineOut(speaker='Mira', text='Back again?')],
        # Even if the LLM mistakenly tries to re-introduce Mira, we ignore it.
        new_characters=[TurnNewCharacterOut(name='mira', race='Human',
                                             gender='female')],
    )

    response = client.post('/api/seed/1/turn',
                           data=json.dumps({'action': 'Greet Mira'}),
                           content_type='application/json')
    assert response.status_code == 200
    assert response.get_json()['new_characters'] == []

    s = session_factory()
    miras = s.query(Character).filter(Character.seed_id == 1,
                                       Character.name.in_(['Mira', 'mira'])).all()
    s.close()
    assert len(miras) == 1


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
