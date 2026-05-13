"""Tests for the ElevenLabs service module and the /api/tts route.

The service is exercised with mocked ``requests`` so the suite never makes
real ElevenLabs calls. The route is mounted on a bare Flask app (mirroring
the other route tests) and the underlying service is stubbed so each test
asserts a single behaviour.
"""
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.orm import Base, Seed, Character, TranscriptEntry
from app.routes import main as main_blueprint
from app.services import elevenlabs_service


# --- Service: helpers --------------------------------------------------------

def test_gender_label_maps_legacy_boolean():
    assert elevenlabs_service._gender_label(True) == 'male'
    assert elevenlabs_service._gender_label(False) == 'female'
    assert elevenlabs_service._gender_label(None) is None


def test_age_bucket_buckets_by_decades():
    now = datetime(2030, 1, 1)
    assert elevenlabs_service._age_bucket(datetime(2010, 1, 1), now) == 'young'
    assert elevenlabs_service._age_bucket(datetime(1990, 1, 1), now) == 'middle_aged'
    assert elevenlabs_service._age_bucket(datetime(1960, 1, 1), now) == 'old'
    assert elevenlabs_service._age_bucket(None, now) is None


# --- Service: find_voice_for_character --------------------------------------

def test_find_voice_returns_none_without_api_key():
    assert elevenlabs_service.find_voice_for_character('') is None


def test_find_voice_returns_none_on_request_exception():
    with patch.object(elevenlabs_service.requests, 'get',
                      side_effect=elevenlabs_service.requests.RequestException('boom')):
        assert elevenlabs_service.find_voice_for_character('k') is None


def test_find_voice_returns_none_on_non_ok():
    fake = MagicMock(ok=False, status_code=500)
    with patch.object(elevenlabs_service.requests, 'get', return_value=fake):
        assert elevenlabs_service.find_voice_for_character('k') is None


def _voices_response(voices, *, has_more=False):
    fake = MagicMock(ok=True)
    fake.json.return_value = {'voices': voices, 'has_more': has_more}
    return fake


def test_find_voice_filters_by_gender_label_client_side():
    """The /v2/voices endpoint ignores ``gender``/``age`` params, so the
    service must score voices client-side from their ``labels`` dict.
    A male character with a female-only candidate must NOT pick her."""
    voices = [
        {'voice_id': 'female-1', 'name': 'Aria', 'labels': {'gender': 'female'}},
        {'voice_id': 'male-1', 'name': 'Roger', 'labels': {'gender': 'male'}},
    ]
    with patch.object(elevenlabs_service.requests, 'get',
                      return_value=_voices_response(voices)):
        vid = elevenlabs_service.find_voice_for_character(
            'k', gender=True,
            date_of_birth=datetime(2000, 1, 1),
            current_dt=datetime(2020, 1, 1),
        )
    assert vid == 'male-1'


def test_find_voice_prefers_voice_matching_description_terms():
    """Free-text hint should bias selection toward voices whose name /
    description / labels contain matching keywords."""
    voices = [
        {'voice_id': 'gentle-vid', 'name': 'Will',
         'labels': {'gender': 'male', 'age': 'young', 'descriptive': 'gentle'}},
        {'voice_id': 'gruff-vid', 'name': 'Brian',
         'labels': {'gender': 'male', 'age': 'young', 'descriptive': 'gruff'},
         'description': 'A gruff baritone, perfect for a blacksmith.'},
    ]
    with patch.object(elevenlabs_service.requests, 'get',
                      return_value=_voices_response(voices)):
        vid = elevenlabs_service.find_voice_for_character(
            'k', gender=True,
            date_of_birth=datetime(2000, 1, 1),
            current_dt=datetime(2020, 1, 1),
            race='human', search_text='gruff blacksmith',
        )
    assert vid == 'gruff-vid'


def test_find_voice_falls_back_when_no_voice_matches_description():
    """When the verbose description matches nothing the relaxed attempt
    must still return a gender-correct voice rather than giving up."""
    voices = [
        {'voice_id': 'male-young',
         'labels': {'gender': 'male', 'age': 'young'}},
        {'voice_id': 'male-old',
         'labels': {'gender': 'male', 'age': 'old'}},
    ]
    with patch.object(elevenlabs_service.requests, 'get',
                      return_value=_voices_response(voices)):
        vid = elevenlabs_service.find_voice_for_character(
            'k', gender=True,
            date_of_birth=datetime(2000, 1, 1),
            current_dt=datetime(2020, 1, 1),
            race='elf',
            search_text='gruff baritone with a faint northern lilt',
        )
    assert vid == 'male-young'


def test_find_voice_returns_none_when_library_is_empty():
    with patch.object(elevenlabs_service.requests, 'get',
                      return_value=_voices_response([])):
        vid = elevenlabs_service.find_voice_for_character(
            'k', gender=True,
            date_of_birth=datetime(2000, 1, 1),
            current_dt=datetime(2020, 1, 1),
            race='elf', search_text='something specific',
        )
    assert vid is None


def test_find_voice_excludes_narrator_voice_from_candidates():
    """Characters must never be assigned the narrator voice, otherwise
    every line collapses onto the same voice at TTS time."""
    voices = [
        {'voice_id': elevenlabs_service.NARRATOR_VOICE_ID,
         'labels': {'gender': 'male'}},
        {'voice_id': 'character-vid', 'labels': {'gender': 'male'}},
    ]
    with patch.object(elevenlabs_service.requests, 'get',
                      return_value=_voices_response(voices)):
        vid = elevenlabs_service.find_voice_for_character('k', gender=True)
    assert vid == 'character-vid'


def test_find_voice_spreads_voices_across_characters_with_distinct_traits():
    """Two characters with different traits must not always collapse to
    the first voice -- the picker uses a stable hash over the inputs to
    spread assignments across the top-scoring tier."""
    voices = [
        {'voice_id': f'male-young-{i}',
         'labels': {'gender': 'male', 'age': 'young'}}
        for i in range(6)
    ]
    chosen = set()
    with patch.object(elevenlabs_service.requests, 'get',
                      return_value=_voices_response(voices)):
        for race in ('elf', 'dwarf', 'human', 'orc'):
            chosen.add(elevenlabs_service.find_voice_for_character(
                'k', gender=True,
                date_of_birth=datetime(2000, 1, 1),
                current_dt=datetime(2020, 1, 1),
                race=race, search_text=f'a {race} of distinction',
            ))
    assert len(chosen) > 1


# --- Service: synthesize -----------------------------------------------------

def test_synthesize_returns_none_for_empty_text():
    assert elevenlabs_service.synthesize('k', 'v', '') is None


def test_synthesize_returns_none_when_no_key_and_no_cache(tmp_path):
    assert elevenlabs_service.synthesize(None, 'v', 'hello',
                                         cache_dir=str(tmp_path)) is None


def test_synthesize_serves_cache_without_api_key(tmp_path):
    path = elevenlabs_service._cache_path(str(tmp_path), 'v', 'hello')
    with open(path, 'wb') as fh:
        fh.write(b'\x00mp3')
    with patch.object(elevenlabs_service.requests, 'post') as post:
        out = elevenlabs_service.synthesize(None, 'v', 'hello', cache_dir=str(tmp_path))
    assert out == b'\x00mp3'
    assert not post.called


def test_synthesize_calls_api_and_writes_cache(tmp_path):
    fake = MagicMock(ok=True, content=b'audio-bytes')
    with patch.object(elevenlabs_service.requests, 'post', return_value=fake):
        out = elevenlabs_service.synthesize('k', 'v', 'hello', cache_dir=str(tmp_path))
    assert out == b'audio-bytes'
    cached = elevenlabs_service._cache_path(str(tmp_path), 'v', 'hello')
    with open(cached, 'rb') as fh:
        assert fh.read() == b'audio-bytes'


# --- Route: /api/tts/<seed_id>/<entry_id> -----------------------------------

@pytest.fixture
def session_factory():
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


@pytest.fixture
def client(session_factory):
    flask_app = Flask(__name__)
    flask_app.config['SESSION_FACTORY'] = session_factory
    flask_app.register_blueprint(main_blueprint)
    return flask_app.test_client()


def _seed_with_entry(session_factory, *, speaker, character_voice_id=None):
    s = session_factory()
    s.add(Seed(id=1, current_turn=1, created_at=datetime.now(), updated_at=datetime.now()))
    s.commit()
    if speaker not in (None, 'Narrator', 'You', 'System'):
        s.add(Character(seed_id=1, main_character=False, alive=True, name=speaker,
                        race='Human', gender=True, level=1, exp_points=0,
                        current_health=10, max_health=10, current_currency=0,
                        voice_id=character_voice_id))
        s.commit()
    s.add(TranscriptEntry(id=42, seed_id=1, kind='narration',
                          speaker=speaker, text='Hello there.'))
    s.commit()
    s.close()


def test_tts_route_returns_404_for_unknown_entry(client):
    resp = client.get('/api/tts/1/9999')
    assert resp.status_code == 404


def test_tts_route_returns_503_when_synthesis_fails(client, session_factory):
    _seed_with_entry(session_factory, speaker='Narrator')
    with patch.object(elevenlabs_service, 'synthesize', return_value=None):
        resp = client.get('/api/tts/1/42')
    assert resp.status_code == 503


def test_tts_route_uses_narrator_voice_for_narrator(client, session_factory):
    _seed_with_entry(session_factory, speaker='Narrator')
    with patch.object(elevenlabs_service, 'synthesize', return_value=b'mp3') as syn:
        resp = client.get('/api/tts/1/42')
    assert resp.status_code == 200
    assert resp.data == b'mp3'
    assert resp.mimetype == 'audio/mpeg'
    assert syn.call_args.args[1] == elevenlabs_service.NARRATOR_VOICE_ID


def test_tts_route_resolves_character_voice_for_named_speaker(client, session_factory):
    _seed_with_entry(session_factory, speaker='Marlow', character_voice_id='vid-marlow')
    with patch.object(elevenlabs_service, 'synthesize', return_value=b'ok') as syn:
        resp = client.get('/api/tts/1/42')
    assert resp.status_code == 200
    assert syn.call_args.args[1] == 'vid-marlow'
