import pytest
from datetime import datetime
from flask import Flask
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.orm import (
    Base, Seed, Character, Location, Event, EventCharacter, Item, CharacterItem,
    Skill, CharacterSkill, Status, CharacterStatus, CharacterRelationship,
    Quest,
)
from app.routes import main as main_blueprint


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


def _seed_world(session_factory):
    s = session_factory()
    seed = Seed(id=1, current_turn=3, created_at=datetime.now(), updated_at=datetime.now())
    s.add(seed)
    s.commit()

    loc = Location(seed_id=1, name='Mock City', description='A test city', type='city')
    # A second location with no MC-involved events; the /api/world locations
    # list should exclude it under the MC-centric filter.
    unvisited = Location(seed_id=1, name='Hidden Vale', description='Untouched',
                         type='wilds')
    s.add_all([loc, unvisited])
    s.commit()

    festival = Event(seed_id=1, name='Mock Festival', description='A test event',
                     type='festival', location_id=loc.id, start_turn=1, end_turn=5)
    npc_only_event = Event(seed_id=1, name='Villager Errand', description='NPC-only',
                           type='errand', location_id=unvisited.id, start_turn=1, end_turn=2)
    s.add_all([festival, npc_only_event])
    s.commit()

    main_char = Character(seed_id=1, main_character=True, alive=True, name='Hero',
                          race='Human', gender=True, level=2, exp_points=10,
                          strength=10, speed=10, agility=10, intelligence=10,
                          wisdom=10, charisma=10, current_health=80, max_health=100,
                          current_currency=50)
    npc = Character(seed_id=1, main_character=False, alive=True, name='Villager',
                    race='Human', gender=False, level=1, exp_points=0,
                    strength=8, speed=8, agility=8, intelligence=8, wisdom=8,
                    charisma=8, current_health=50, max_health=50, current_currency=5)
    s.add_all([main_char, npc])
    s.commit()

    # The festival involves the main character; the errand only involves the
    # NPC. The /api/world events list should surface only the former.
    s.add(EventCharacter(seed_id=1, character_id=main_char.id,
                         event_id=festival.id, role='participant'))
    s.add(EventCharacter(seed_id=1, character_id=npc.id,
                         event_id=npc_only_event.id, role='participant'))
    s.commit()

    item = Item(name='Sword', description='Sharp', type='weapon', value=10.0, weight=2.0)
    skill = Skill(name='Swordsmanship', description='Wielding swords')
    status = Status(name='Blessed', description='Divine favor', type='buff', duration=10.0)
    s.add_all([item, skill, status])
    s.commit()

    s.add(CharacterItem(seed_id=1, character_id=main_char.id, item_id=item.id,
                        quantity=1, condition=1.0))
    s.add(CharacterSkill(seed_id=1, character_id=main_char.id, skill_id=skill.id,
                         level=3, exp_points=100))
    s.add(CharacterStatus(seed_id=1, character_id=main_char.id, status_id=status.id,
                          active=True, end_date_time=datetime.now()))
    s.add(CharacterRelationship(seed_id=1, character_id=main_char.id,
                                related_character_id=npc.id, relationship_type='friend',
                                familiarity=6))
    s.add(Quest(seed_id=1, name='Find the Relic', description='Recover lost item',
                currency_reward=100, exp_reward=200))
    s.commit()
    s.close()


def test_get_world_returns_full_payload(client, session_factory):
    _seed_world(session_factory)

    response = client.get('/api/world/1')
    assert response.status_code == 200
    payload = response.get_json()

    assert payload['seed_id'] == 1
    assert payload['current_turn'] == 3

    assert payload['main_character']['name'] == 'Hero'
    assert payload['main_character']['level'] == 2

    assert len(payload['locations']) == 1
    assert payload['locations'][0]['name'] == 'Mock City'

    assert len(payload['events']) == 1
    assert payload['events'][0]['name'] == 'Mock Festival'

    # Surrounding characters list excludes the main character and now carries
    # acquaintance metadata derived from the MC's CharacterRelationship rows.
    assert len(payload['characters']) == 1
    villager = payload['characters'][0]
    assert villager['name'] == 'Villager'
    assert villager['familiarity'] == 6
    assert villager['acquaintance_level'] == 'acquainted'
    assert villager['display_name'] == 'Villager'
    assert villager['display_icon']  # non-empty

    assert len(payload['items']) == 1
    assert payload['items'][0]['name'] == 'Sword'

    assert len(payload['skills']) == 1
    assert payload['skills'][0]['name'] == 'Swordsmanship'

    assert len(payload['statuses']) == 1
    assert payload['statuses'][0]['name'] == 'Blessed'

    assert len(payload['relationships']) == 1
    rel = payload['relationships'][0]
    assert rel['name'] == 'Villager'
    assert rel['familiarity'] == 6
    assert rel['acquaintance_level'] == 'acquainted'

    assert len(payload['quests']) == 1
    assert payload['quests'][0]['name'] == 'Find the Relic'

    # Stats are derived from the main character columns
    stat_names = {s['name'] for s in payload['stats']}
    assert {'Strength', 'Current Health', 'Max Health', 'Currency', 'Level'} <= stat_names


def test_get_world_returns_404_for_unknown_seed(client):
    response = client.get('/api/world/9999')
    assert response.status_code == 404
    assert response.get_json()['error'] == 'Seed not found'


def test_list_seeds_returns_all(client, session_factory):
    _seed_world(session_factory)

    response = client.get('/api/seeds')
    assert response.status_code == 200
    seeds = response.get_json()['seeds']

    assert len(seeds) == 1
    assert seeds[0]['seed_id'] == 1
    assert seeds[0]['main_character_name'] == 'Hero'
    assert seeds[0]['current_turn'] == 3



def test_get_world_marks_unmet_npcs_as_strangers(client, session_factory):
    # Seed an NPC with no MC<->NPC relationship row so the read path must
    # treat them as a stranger (familiarity 0, anonymized display fields).
    s = session_factory()
    s.add(Seed(id=2, current_turn=1, created_at=datetime.now(), updated_at=datetime.now()))
    s.commit()
    main_char = Character(seed_id=2, main_character=True, alive=True, name='Hero',
                          race='Human', gender=True, level=1, exp_points=0,
                          strength=10, speed=10, agility=10, intelligence=10,
                          wisdom=10, charisma=10, current_health=100, max_health=100,
                          current_currency=0)
    npc = Character(seed_id=2, main_character=False, alive=True, name='Marlow',
                    race='Elf', gender=True, level=2, exp_points=0,
                    strength=8, speed=8, agility=8, intelligence=8, wisdom=8,
                    charisma=8, current_health=50, max_health=50, current_currency=0)
    s.add_all([main_char, npc])
    s.commit()
    s.close()

    response = client.get('/api/world/2')
    assert response.status_code == 200
    payload = response.get_json()

    assert len(payload['characters']) == 1
    stranger = payload['characters'][0]
    assert stranger['name'] == 'Marlow'  # underlying name is preserved
    assert stranger['display_name'] == 'Stranger'
    assert stranger['acquaintance_level'] == 'unknown'
    assert stranger['familiarity'] == 0
    # Description must not leak race/level for an unmet NPC.
    assert 'Elf' not in stranger['description']
    assert 'Level' not in stranger['description']

    assert payload['relationships'] == []
