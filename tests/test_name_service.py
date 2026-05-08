import json
from unittest.mock import MagicMock

import pytest

from app.orm import NameLibrary
from app.services.name_service import NameService
from app.world_building.schemas import NamingThemeChoice, NamingThemeSelectionOut


def _add(session, **overrides):
    row = {
        'source': 'fantasynames',
        'theme': 'elf',
        'gender': 'any',
        'category': 'first',
        'name': 'Aelar',
    }
    row.update(overrides)
    session.add(NameLibrary(**row))


@pytest.fixture
def populated_library(db_session):
    _add(db_session, source='fantasynames', theme='elf', gender='male',
         category='first', name='Aelar')
    _add(db_session, source='fantasynames', theme='elf', gender='female',
         category='first', name='Lirien')
    _add(db_session, source='fantasynames', theme='elf', gender='any',
         category='last', name='Highroot')
    _add(db_session, source='pynames', theme='scandinavian', gender='male',
         category='first', name='Sorli')
    db_session.commit()
    return db_session


def test_list_available_themes_returns_distinct_pairs(populated_library):
    service = NameService(populated_library)
    themes = service.list_available_themes()
    assert {'source': 'fantasynames', 'theme': 'elf'} in themes
    assert {'source': 'pynames', 'theme': 'scandinavian'} in themes
    assert len(themes) == 2


def test_list_available_themes_empty_table(db_session):
    service = NameService(db_session)
    assert service.list_available_themes() == []


def test_select_themes_for_seed_no_gpt_returns_empty(populated_library):
    service = NameService(populated_library, gpt_service=None)
    assert service.select_themes_for_seed({'theme': 'fantasy'}) == []


def test_select_themes_for_seed_filters_hallucinations(populated_library):
    gpt = MagicMock()
    gpt.get_structured.return_value = NamingThemeSelectionOut(themes=[
        NamingThemeChoice(source='fantasynames', theme='elf'),
        NamingThemeChoice(source='fantasynames', theme='dragonborn'),  # not in DB
    ])
    service = NameService(populated_library, gpt_service=gpt)
    chosen = service.select_themes_for_seed({'theme': 'fantasy'})
    assert chosen == [{'source': 'fantasynames', 'theme': 'elf'}]


def test_select_themes_caps_at_three(populated_library):
    # Seed enough distinct themes that the LLM could pick 4+.
    _add(populated_library, source='pynames', theme='korean', name='Min')
    _add(populated_library, source='pynames', theme='mongolian', name='Bat')
    _add(populated_library, source='nomina', theme='dwarf', name='Thorin')
    populated_library.commit()

    gpt = MagicMock()
    gpt.get_structured.return_value = NamingThemeSelectionOut(themes=[
        NamingThemeChoice(source='fantasynames', theme='elf'),
        NamingThemeChoice(source='pynames', theme='scandinavian'),
        NamingThemeChoice(source='pynames', theme='korean'),
        NamingThemeChoice(source='pynames', theme='mongolian'),
    ])
    service = NameService(populated_library, gpt_service=gpt)
    assert len(service.select_themes_for_seed({'theme': 'fantasy'})) == 3


def test_assign_and_get_themes_round_trip(populated_library, seed_in_db):
    service = NameService(populated_library)
    themes = [{'source': 'fantasynames', 'theme': 'elf'}]
    service.assign_themes_to_seed(seed_in_db.id, themes)

    populated_library.refresh(seed_in_db)
    assert json.loads(seed_in_db.naming_themes) == themes
    assert service.get_themes_for_seed(seed_in_db.id) == themes


def test_assign_empty_themes_clears_column(populated_library, seed_in_db):
    service = NameService(populated_library)
    service.assign_themes_to_seed(seed_in_db.id,
                                  [{'source': 'fantasynames', 'theme': 'elf'}])
    service.assign_themes_to_seed(seed_in_db.id, [])
    populated_library.refresh(seed_in_db)
    assert seed_in_db.naming_themes is None
    assert service.get_themes_for_seed(seed_in_db.id) == []


def test_get_themes_for_seed_handles_corrupt_json(populated_library, seed_in_db):
    seed_in_db.naming_themes = "{not valid json"
    populated_library.commit()
    service = NameService(populated_library)
    assert service.get_themes_for_seed(seed_in_db.id) == []


def test_random_name_no_themes_returns_none(populated_library):
    assert NameService(populated_library).random_name([]) is None


def test_random_name_exact_match(populated_library):
    service = NameService(populated_library)
    name = service.random_name(
        [{'source': 'fantasynames', 'theme': 'elf'}],
        gender='male', category='first')
    assert name == 'Aelar'


def test_random_name_falls_back_when_gender_missing(db_session):
    # Only an 'any'-gender row exists for this theme.
    db_session.add(NameLibrary(source='fantasynames', theme='dwarf',
                               gender='any', category='first', name='Thordra'))
    db_session.commit()
    service = NameService(db_session)
    name = service.random_name(
        [{'source': 'fantasynames', 'theme': 'dwarf'}],
        gender='male', category='first')
    assert name == 'Thordra'


def test_random_name_falls_back_when_category_missing(db_session):
    # No 'last'-category row exists for this theme; service must drop the
    # category filter and surface the first-name row.
    db_session.add(NameLibrary(source='fantasynames', theme='hobbit',
                               gender='female', category='first', name='Rosie'))
    db_session.commit()
    service = NameService(db_session)
    name = service.random_name(
        [{'source': 'fantasynames', 'theme': 'hobbit'}],
        gender='female', category='last')
    assert name == 'Rosie'


def test_random_name_returns_none_when_themes_unmatched(populated_library):
    service = NameService(populated_library)
    name = service.random_name(
        [{'source': 'nomina', 'theme': 'orc'}],
        gender='male', category='first')
    assert name is None


def test_normalize_gender_handles_bool_and_strings():
    assert NameService._normalize_gender(True) == 'male'
    assert NameService._normalize_gender(False) == 'female'
    assert NameService._normalize_gender('Male') == 'male'
    assert NameService._normalize_gender('nonbinary') == 'any'
    assert NameService._normalize_gender(None) == 'any'
