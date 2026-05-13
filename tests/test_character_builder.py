import pytest
from unittest.mock import MagicMock

from app.orm import Character, Event, EventCharacter, NameLibrary
from app.services.name_service import NameService
from app.world_building.character_builder import CharacterBuilder
from app.world_building.schemas import EventOut, MainCharacterOut


@pytest.fixture
def seed_data():
    return {"theme": "fantasy"}


@pytest.fixture
def character_builder(seed_data, db_session, gpt_service, seed_in_db):
    return CharacterBuilder(seed_data, seed_in_db.id, db_session, gpt_service)


@pytest.mark.llm
def test_create_main_character_success(character_builder):
    result = character_builder.create_main_character()

    assert result["status"] == "success"
    assert result["message"] == "Main character created successfully"
    assert "id" in character_builder.character_data
    for stat in ['strength', 'speed', 'agility', 'intelligence', 'wisdom', 'charisma']:
        assert character_builder.character_data[stat] is not None


def test_create_main_character_failure(seed_data, db_session, seed_in_db):
    # Failure-path unit test: stub get_structured to simulate exhausted retries.
    bad_service = MagicMock()
    bad_service.get_structured.return_value = None

    builder = CharacterBuilder(seed_data, seed_in_db.id, db_session, bad_service)
    result = builder.create_main_character()

    assert result["status"] == "failure"
    assert "Failed to create main character due to invalid data" in result["message"]


def test_create_surrounding_characters_no_locations(seed_data, db_session, seed_in_db):
    # No LLM calls should be made when there are no locations to populate.
    builder = CharacterBuilder(seed_data, seed_in_db.id, db_session, MagicMock())
    builder.locations = []

    result = builder.create_surrounding_characters()
    assert result["status"] == "success"
    assert hasattr(builder, 'NPCs_data')


# --------------------------------------------------------------------- #
# _seed_data_has_name                                                    #
# --------------------------------------------------------------------- #
@pytest.mark.parametrize("seed,expected", [
    ({"character_name": "Lyra"}, True),
    ({"name": "Bram"}, True),
    ({"main_character_name": "Eden"}, True),
    ({"character_name": "   "}, False),
    ({"theme": "fantasy"}, False),
    ({}, False),
    (None, False),
    ("not a dict", False),
    # The frontend posts seed_data as a JSON string; accept that shape too.
    ('{"character_name": "Lyra"}', True),
    ('{"theme": "fantasy"}', False),
    ('{"character_name": "   "}', False),
])
def test_seed_data_has_name(seed, expected):
    assert CharacterBuilder._seed_data_has_name(seed) is expected


# --------------------------------------------------------------------- #
# Seeded-name override on create_main_character                          #
# --------------------------------------------------------------------- #
def _make_main_payload(name="LLM_Name", gender=True):
    return MainCharacterOut(name=name, gender=gender, race="elf",
                            skills=[], statuses=[])


def _stub_gpt_returning(payload):
    gpt = MagicMock()
    gpt.get_structured.return_value = payload
    return gpt


@pytest.fixture
def elf_library(db_session):
    db_session.add_all([
        NameLibrary(source='fantasynames', theme='elf', gender='male',
                    category='first', name='Aelar'),
        NameLibrary(source='fantasynames', theme='elf', gender='female',
                    category='first', name='Lirien'),
    ])
    db_session.commit()
    return db_session


def _name_service_with_themes(session, seed_id, themes):
    svc = NameService(session)
    svc.assign_themes_to_seed(seed_id, themes)
    return svc


def test_create_main_character_uses_library_name_when_seed_has_no_name(
        elf_library, seed_in_db):
    name_service = _name_service_with_themes(
        elf_library, seed_in_db.id,
        [{'source': 'fantasynames', 'theme': 'elf'}])
    gpt = _stub_gpt_returning(_make_main_payload(name="LLM_Name", gender=True))

    builder = CharacterBuilder({"theme": "fantasy"}, seed_in_db.id,
                               elf_library, gpt, name_service=name_service)
    result = builder.create_main_character()

    assert result["status"] == "success"
    assert builder.character_data['name'] == 'Aelar'
    persisted = elf_library.query(Character).filter_by(
        id=builder.character_data['id']).one()
    assert persisted.name == 'Aelar'


def test_create_main_character_respects_user_provided_name(elf_library, seed_in_db):
    name_service = _name_service_with_themes(
        elf_library, seed_in_db.id,
        [{'source': 'fantasynames', 'theme': 'elf'}])
    gpt = _stub_gpt_returning(_make_main_payload(name="LLM_Name", gender=True))

    builder = CharacterBuilder({"character_name": "Hero"}, seed_in_db.id,
                               elf_library, gpt, name_service=name_service)
    result = builder.create_main_character()

    assert result["status"] == "success"
    assert builder.character_data['name'] == 'Hero'
    persisted = elf_library.query(Character).filter_by(
        id=builder.character_data['id']).one()
    assert persisted.name == 'Hero'


def test_create_main_character_respects_user_name_from_json_string(
        elf_library, seed_in_db):
    # Mirrors the production HTTP path: the frontend JSON.stringifies the
    # form payload, so seed_data lands here as a string rather than a dict.
    name_service = _name_service_with_themes(
        elf_library, seed_in_db.id,
        [{'source': 'fantasynames', 'theme': 'elf'}])
    gpt = _stub_gpt_returning(_make_main_payload(name="LLM_Name", gender=True))

    seed_data = '{"character_name": "Hero", "character_age": "30", "character_gender": "male"}'
    builder = CharacterBuilder(seed_data, seed_in_db.id,
                               elf_library, gpt, name_service=name_service)
    result = builder.create_main_character()

    assert result["status"] == "success"
    assert builder.character_data['name'] == 'Hero'


def test_create_main_character_keeps_llm_name_when_no_name_service(
        elf_library, seed_in_db):
    gpt = _stub_gpt_returning(_make_main_payload(name="LLM_Name", gender=True))
    builder = CharacterBuilder({"theme": "fantasy"}, seed_in_db.id,
                               elf_library, gpt)  # no name_service
    result = builder.create_main_character()

    assert result["status"] == "success"
    assert builder.character_data['name'] == 'LLM_Name'


def test_create_main_character_keeps_llm_name_when_no_themes_assigned(
        elf_library, seed_in_db):
    # Library is populated but the seed has no themes -> nothing to override with.
    name_service = NameService(elf_library)
    gpt = _stub_gpt_returning(_make_main_payload(name="LLM_Name", gender=True))

    builder = CharacterBuilder({"theme": "fantasy"}, seed_in_db.id,
                               elf_library, gpt, name_service=name_service)
    result = builder.create_main_character()

    assert result["status"] == "success"
    assert builder.character_data['name'] == 'LLM_Name'


def test_create_main_character_keeps_llm_name_when_library_has_no_match(
        db_session, seed_in_db):
    # Themes assigned but library is empty -> random_name returns None.
    name_service = _name_service_with_themes(
        db_session, seed_in_db.id,
        [{'source': 'fantasynames', 'theme': 'elf'}])
    gpt = _stub_gpt_returning(_make_main_payload(name="LLM_Name", gender=True))

    builder = CharacterBuilder({"theme": "fantasy"}, seed_in_db.id,
                               db_session, gpt, name_service=name_service)
    result = builder.create_main_character()

    assert result["status"] == "success"
    assert builder.character_data['name'] == 'LLM_Name'


# --------------------------------------------------------------------- #
# create_opening_event                                                   #
# --------------------------------------------------------------------- #
def test_create_opening_event_persists_event_and_mc_link(seed_data, db_session, seed_in_db):
    # Persist a Character row so character_data['id'] points at a real MC.
    mc = Character(seed_id=seed_in_db.id, main_character=True, alive=True,
                   name='Hero', race='Human', gender=True, level=1)
    db_session.add(mc)
    db_session.commit()

    gpt = _stub_gpt_returning(EventOut(
        name='Tavern Brawl', description='Fists fly over a spilled drink.',
        type='conflict', role='participant',
    ))
    builder = CharacterBuilder(seed_data, seed_in_db.id, db_session, gpt)
    builder.character_data = {'id': mc.id, 'name': 'Hero',
                              'current_date_time': None}
    builder.locations = [{'id': 1, 'name': 'Hamlet'}]

    # The starting location row has to exist for the FK on Event to resolve.
    from app.orm import Location
    db_session.add(Location(id=1, seed_id=seed_in_db.id, name='Hamlet',
                            type='village'))
    db_session.commit()

    result = builder.create_opening_event()

    assert result['status'] == 'success'
    event = db_session.query(Event).filter_by(id=result['event_id']).one()
    assert event.name == 'Tavern Brawl'
    assert event.location_id == 1
    link = db_session.query(EventCharacter).filter_by(event_id=event.id).one()
    assert link.character_id == mc.id
    assert link.role == 'participant'
    # The persisted event payload is exposed so the intro narrator can
    # anchor the opening prose to it instead of drifting into a contradictory
    # calm scene while the events panel shows the same crisis.
    assert builder.opening_event['name'] == 'Tavern Brawl'
    assert builder.opening_event['role'] == 'participant'
    assert builder.opening_event['location_id'] == 1


def test_create_opening_event_skips_without_main_character(seed_data, db_session, seed_in_db):
    builder = CharacterBuilder(seed_data, seed_in_db.id, db_session, MagicMock())
    builder.locations = [{'id': 1, 'name': 'Hamlet'}]

    result = builder.create_opening_event()
    assert result['status'] == 'skipped'


def test_create_opening_event_skips_without_locations(seed_data, db_session, seed_in_db):
    builder = CharacterBuilder(seed_data, seed_in_db.id, db_session, MagicMock())
    builder.character_data = {'id': 99, 'name': 'Hero'}
    builder.locations = []

    result = builder.create_opening_event()
    assert result['status'] == 'skipped'


def test_create_opening_event_returns_failure_on_llm_none(seed_data, db_session, seed_in_db):
    gpt = _stub_gpt_returning(None)
    builder = CharacterBuilder(seed_data, seed_in_db.id, db_session, gpt)
    builder.character_data = {'id': 99, 'name': 'Hero'}
    builder.locations = [{'id': 1, 'name': 'Hamlet'}]

    result = builder.create_opening_event()
    assert result['status'] == 'failure'
