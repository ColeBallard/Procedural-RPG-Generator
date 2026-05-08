import pytest
from unittest.mock import MagicMock

from app.orm import Character, NameLibrary
from app.services.name_service import NameService
from app.world_building.character_builder import CharacterBuilder
from app.world_building.schemas import MainCharacterOut


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
    # The user-supplied name is on the seed_data, but create_main_character
    # itself only short-circuits the library override; the LLM-generated
    # name is what flows into the row.
    assert builder.character_data['name'] == 'LLM_Name'


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
