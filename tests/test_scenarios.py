"""Tests for the scenario handlers (dialogue, battle, trade).

Covers the deterministic surface of each handler: ``start`` enrols the
right participants, ``apply_action`` enforces verb whitelists / role
checks, and resolution closes the scenario + persists side effects
(HP for battle, item / currency transfers for trade, relationship
deltas for dialogue).
"""
import pytest

from app.orm import (
    Character, CharacterItem, CharacterRelationship, Item, Scenario, Seed,
)
from app.scenarios import (
    HANDLERS, KIND_BATTLE, KIND_DIALOGUE, KIND_TRADE,
    active_scenario_for, get_handler, scenario_view,
)
from app.world_building.schemas import ScenarioTriggerOut


@pytest.fixture
def seed_with_party(db_session):
    """Player + a single NPC + a Seed; reused by all three scenarios."""
    db_session.add(Seed(id=1, current_turn=1))
    db_session.commit()
    mc = Character(seed_id=1, main_character=True, alive=True, name='Hero',
                   level=3, strength=14, agility=10, speed=12,
                   current_health=30, max_health=30, current_currency=50)
    npc = Character(seed_id=1, main_character=False, alive=True, name='Marek',
                    level=2, strength=12, agility=8, speed=10,
                    current_health=20, max_health=20, current_currency=30)
    db_session.add_all([mc, npc])
    db_session.commit()
    return {'mc': mc, 'npc': npc}


def _trigger(kind, participants, reason='Test'):
    return ScenarioTriggerOut(kind=kind, participants=participants, reason=reason)


# --- Registry & substrate ---------------------------------------------------

def test_handlers_registered_for_each_kind():
    assert set(HANDLERS) == {KIND_DIALOGUE, KIND_BATTLE, KIND_TRADE}
    for kind in HANDLERS:
        assert get_handler(kind) is HANDLERS[kind]
    assert get_handler('unknown') is None


def test_active_scenario_for_returns_none_when_idle(db_session, seed_with_party):
    assert active_scenario_for(db_session, 1) is None


def test_start_drops_trigger_with_no_resolvable_participants(db_session, seed_with_party):
    h = get_handler(KIND_DIALOGUE)
    out = h.start(db_session, 1, _trigger(KIND_DIALOGUE, ['Nobody Real']),
                  current_turn=1)
    assert out is None
    assert active_scenario_for(db_session, 1) is None


# --- Dialogue ---------------------------------------------------------------

def test_dialogue_start_enrols_player_and_npc(db_session, seed_with_party):
    h = get_handler(KIND_DIALOGUE)
    sc = h.start(db_session, 1, _trigger(KIND_DIALOGUE, ['Marek']),
                 current_turn=1)
    assert sc is not None
    view = scenario_view(db_session, sc)
    assert view['player']['name'] == 'Hero'
    assert view['npcs'][0]['name'] == 'Marek'
    assert 'say' in view['verbs']


def test_dialogue_unknown_verb_returns_400(db_session, seed_with_party):
    h = get_handler(KIND_DIALOGUE)
    sc = h.start(db_session, 1, _trigger(KIND_DIALOGUE, ['Marek']))
    body, status = h.apply_action(db_session, sc, {'verb': 'meditate'})
    assert status == 400
    assert 'Unknown' in body['error']


def test_dialogue_persuade_applies_relationship_bias(db_session, seed_with_party, session_factory):
    h = get_handler(KIND_DIALOGUE)
    sc = h.start(db_session, 1, _trigger(KIND_DIALOGUE, ['Marek']))
    body, status = h.apply_action(
        db_session, sc, {'verb': 'persuade', 'text': 'Trust me on this.'},
        session_factory=session_factory, current_turn=1,
    )
    assert status == 200
    assert body['resolved'] is False
    # Verb bias for persuade -> familiarity +1, respect +1.
    rel = (db_session.query(CharacterRelationship)
           .filter(CharacterRelationship.character_id == seed_with_party['npc'].id,
                   CharacterRelationship.related_character_id == seed_with_party['mc'].id)
           .first())
    assert rel is not None
    assert rel.familiarity == 1 + 1  # default 1, +1 from bias
    assert rel.respect == 5 + 1
    assert body['deltas']['respect'] == 1


def test_dialogue_leave_resolves_scenario(db_session, seed_with_party, session_factory):
    h = get_handler(KIND_DIALOGUE)
    sc = h.start(db_session, 1, _trigger(KIND_DIALOGUE, ['Marek']))
    body, status = h.apply_action(db_session, sc, {'verb': 'leave'},
                                   session_factory=session_factory,
                                   current_turn=1)
    assert status == 200
    assert body['resolved'] is True
    db_session.refresh(sc)
    assert sc.status == 'resolved'
    assert active_scenario_for(db_session, 1) is None


# --- Battle -----------------------------------------------------------------

def test_battle_start_enrols_combatants_with_initiative(db_session, seed_with_party):
    h = get_handler(KIND_BATTLE)
    sc = h.start(db_session, 1, _trigger(KIND_BATTLE, ['Marek']),
                 current_turn=1)
    assert sc is not None
    view = scenario_view(db_session, sc)
    assert view['player']['hp'] == 30
    assert view['opponent']['hp'] == 20
    # Hero has speed 12 -> acts first.
    assert view['active_id'] == seed_with_party['mc'].id


def test_battle_attack_damages_opponent(db_session, seed_with_party, session_factory):
    h = get_handler(KIND_BATTLE)
    sc = h.start(db_session, 1, _trigger(KIND_BATTLE, ['Marek']))
    body, status = h.apply_action(
        db_session, sc, {'verb': 'attack'},
        session_factory=session_factory, current_turn=1,
    )
    assert status == 200
    assert body['view']['opponent']['hp'] < 20
    # Persisted to the Character row so the rest of the game sees the damage.
    db_session.refresh(seed_with_party['npc'])
    assert seed_with_party['npc'].current_health == body['view']['opponent']['hp']


def test_battle_flee_resolves_immediately(db_session, seed_with_party, session_factory):
    h = get_handler(KIND_BATTLE)
    sc = h.start(db_session, 1, _trigger(KIND_BATTLE, ['Marek']))
    body, status = h.apply_action(
        db_session, sc, {'verb': 'flee'},
        session_factory=session_factory, current_turn=1,
    )
    assert status == 200
    assert body['resolved'] is True
    assert 'fled' in body['summary'].lower()


# --- Trade ------------------------------------------------------------------

@pytest.fixture
def party_with_inventory(db_session, seed_with_party):
    """Add an item to each side so the trade scenario has something to move."""
    sword = Item(name='Sword', type='weapon', value=10.0, weight=3.0)
    potion = Item(name='Potion', type='consumable', value=5.0, weight=0.1)
    db_session.add_all([sword, potion])
    db_session.flush()
    player_sword = CharacterItem(seed_id=1, character_id=seed_with_party['mc'].id,
                                  item_id=sword.id, quantity=1, condition=1.0)
    merchant_potion = CharacterItem(seed_id=1, character_id=seed_with_party['npc'].id,
                                     item_id=potion.id, quantity=4, condition=1.0)
    db_session.add_all([player_sword, merchant_potion])
    db_session.commit()
    return {**seed_with_party, 'sword': sword, 'potion': potion,
            'player_sword': player_sword, 'merchant_potion': merchant_potion}


def test_trade_start_enrols_player_and_merchant(db_session, party_with_inventory):
    h = get_handler(KIND_TRADE)
    sc = h.start(db_session, 1, _trigger(KIND_TRADE, ['Marek']),
                 current_turn=1)
    assert sc is not None
    view = scenario_view(db_session, sc)
    assert view['player']['name'] == 'Hero'
    assert view['merchant']['name'] == 'Marek'
    inv_names = {row['name'] for row in view['player']['inventory']}
    assert 'Sword' in inv_names


def test_trade_add_basket_updates_value(db_session, party_with_inventory, session_factory):
    h = get_handler(KIND_TRADE)
    sc = h.start(db_session, 1, _trigger(KIND_TRADE, ['Marek']))
    body, status = h.apply_action(
        db_session, sc,
        {'verb': 'add', 'side': 'player',
         'character_item_id': party_with_inventory['player_sword'].id,
         'quantity': 1},
        session_factory=session_factory,
    )
    assert status == 200
    assert body['view']['basket_value']['player'] == 10  # one sword @ 10


def test_trade_add_rejects_item_not_held_by_side(db_session, party_with_inventory, session_factory):
    h = get_handler(KIND_TRADE)
    sc = h.start(db_session, 1, _trigger(KIND_TRADE, ['Marek']))
    # The merchant's potion isn't on the player's side; this must 400.
    body, status = h.apply_action(
        db_session, sc,
        {'verb': 'add', 'side': 'player',
         'character_item_id': party_with_inventory['merchant_potion'].id,
         'quantity': 1},
        session_factory=session_factory,
    )
    assert status == 400
    assert 'isn' in body['error'].lower()  # "isn't held by the chosen side"


def test_trade_set_currency_clamps_to_wallet(db_session, party_with_inventory, session_factory):
    h = get_handler(KIND_TRADE)
    sc = h.start(db_session, 1, _trigger(KIND_TRADE, ['Marek']))
    # Hero only carries 50; asking for 999 must clamp.
    body, status = h.apply_action(
        db_session, sc,
        {'verb': 'set_currency', 'side': 'player', 'amount': 999},
        session_factory=session_factory,
    )
    assert status == 200
    assert body['view']['player']['currency_offered'] == 50


def test_trade_propose_with_fair_basket_settles_and_transfers(
        db_session, party_with_inventory, session_factory):
    h = get_handler(KIND_TRADE)
    sc = h.start(db_session, 1, _trigger(KIND_TRADE, ['Marek']))
    # Player offers the sword (value 10); merchant offers 2 potions (value 10).
    h.apply_action(db_session, sc,
                   {'verb': 'add', 'side': 'player',
                    'character_item_id': party_with_inventory['player_sword'].id,
                    'quantity': 1},
                   session_factory=session_factory)
    h.apply_action(db_session, sc,
                   {'verb': 'add', 'side': 'merchant',
                    'character_item_id': party_with_inventory['merchant_potion'].id,
                    'quantity': 2},
                   session_factory=session_factory)

    body, status = h.apply_action(db_session, sc, {'verb': 'propose'},
                                   session_factory=session_factory,
                                   current_turn=1)
    assert status == 200
    assert body['resolved'] is True
    db_session.refresh(sc)
    assert sc.status == 'resolved'
    # Sword now belongs to the merchant.
    sword_now = (db_session.query(CharacterItem)
                 .filter(CharacterItem.id == party_with_inventory['player_sword'].id)
                 .first())
    assert sword_now.character_id == party_with_inventory['npc'].id
    # Player picked up 2 potions (a new CharacterItem row owned by the player).
    player_potions = (db_session.query(CharacterItem)
                      .filter(CharacterItem.character_id == party_with_inventory['mc'].id,
                              CharacterItem.item_id == party_with_inventory['potion'].id)
                      .first())
    assert player_potions is not None
    assert player_potions.quantity == 2
    # Merchant's potion stack shrunk by the 2 they handed over.
    merchant_potion = (db_session.query(CharacterItem)
                       .filter(CharacterItem.id == party_with_inventory['merchant_potion'].id)
                       .first())
    assert merchant_potion.quantity == 2  # 4 - 2


def test_trade_propose_unfair_basket_keeps_scenario_open(
        db_session, party_with_inventory, session_factory):
    h = get_handler(KIND_TRADE)
    sc = h.start(db_session, 1, _trigger(KIND_TRADE, ['Marek']))
    # Player offers nothing, merchant offers a potion -> rejected.
    h.apply_action(db_session, sc,
                   {'verb': 'add', 'side': 'merchant',
                    'character_item_id': party_with_inventory['merchant_potion'].id,
                    'quantity': 1},
                   session_factory=session_factory)
    body, status = h.apply_action(db_session, sc, {'verb': 'propose'},
                                   session_factory=session_factory,
                                   current_turn=1)
    assert status == 200
    assert body['resolved'] is False
    db_session.refresh(sc)
    assert sc.status == 'active'


def test_trade_leave_aborts_scenario(db_session, party_with_inventory, session_factory):
    h = get_handler(KIND_TRADE)
    sc = h.start(db_session, 1, _trigger(KIND_TRADE, ['Marek']))
    body, status = h.apply_action(db_session, sc, {'verb': 'leave'},
                                   session_factory=session_factory,
                                   current_turn=1)
    assert status == 200
    assert body['resolved'] is True
    db_session.refresh(sc)
    assert sc.status == 'aborted'
