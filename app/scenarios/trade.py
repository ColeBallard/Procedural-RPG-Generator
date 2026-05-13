"""Trade scenario: structured barter between the player and a merchant.

State shape::

    {
        "basket_player": {<character_item_id>: <qty>},   # offered by player
        "basket_merchant": {<character_item_id>: <qty>}, # offered by merchant
        "currency_player": <int>,    # currency player adds to their offer
        "currency_merchant": <int>,  # currency merchant adds to theirs
        "last_haggle": {"accept": bool, "price_adjustment": int,
                         "reply": "..."},  # most recent merchant verdict
        "log": [{"actor": "...", "verb": "...", "text": "..."}, ...],
    }

Verbs accepted via ``apply_action``::

    {"verb": "add"|"remove"|"set_currency"|"propose"|"haggle"|"leave",
     "side": "player"|"merchant",      # for add/remove/set_currency
     "character_item_id": <int>,       # for add/remove
     "quantity": <int>,                # for add/remove
     "amount": <int>,                  # for set_currency
     "pitch": "<player line>"}         # for haggle
"""
from __future__ import annotations

import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.orm import (
    Character, CharacterItem, CharacterRelationship, Item,
)
from app.prompt_templates import SCENARIO_PROMPTS
from app.services import transcript_service

from .base import (
    KIND_TRADE, ScenarioHandler, add_participant, load_state,
    lookup_characters_by_name, participants_by_role, resolve, save_state,
)


TRADE_VERBS = {'add', 'remove', 'set_currency', 'propose', 'haggle', 'leave'}


class TradeHaggleOut(BaseModel):
    """LLM payload for the merchant's haggle verdict."""
    accept: bool = False
    price_adjustment: int = 0
    reply: str = ''


class TradeHandler(ScenarioHandler):
    kind = KIND_TRADE

    # ----- substrate hooks --------------------------------------------------

    def start(self, db_session, seed_id, trigger, *, current_turn=None,
              session_factory=None, gpt_service=None):
        from app.orm import Scenario
        merchants = lookup_characters_by_name(db_session, seed_id,
                                              trigger.participants)
        if not merchants:
            return None
        mc = (
            db_session.query(Character)
            .filter(Character.seed_id == seed_id,
                    Character.main_character == True)  # noqa: E712
            .first()
        )
        if mc is None:
            return None

        scenario = Scenario(
            seed_id=seed_id, kind=self.kind, status='active',
            turn_started=current_turn, summary=trigger.reason or '',
        )
        db_session.add(scenario)
        db_session.flush()
        add_participant(db_session, scenario, mc.id, 'player', 0)
        add_participant(db_session, scenario, merchants[0].id, 'merchant', 1)

        save_state(db_session, scenario, {
            'basket_player': {},
            'basket_merchant': {},
            'currency_player': 0,
            'currency_merchant': 0,
            'last_haggle': None,
            'log': [],
        })
        db_session.commit()
        db_session.refresh(scenario)
        return scenario

    def apply_action(self, db_session, scenario, action, *,
                     gpt_service=None, session_factory=None,
                     current_turn=None):
        verb = (action.get('verb') or '').strip().lower()
        if verb not in TRADE_VERBS:
            return {'error': f'Unknown trade verb: {verb}'}, 400

        roles = participants_by_role(db_session, scenario)
        player = (roles.get('player') or [None])[0]
        merchant = (roles.get('merchant') or [None])[0]
        if player is None or merchant is None:
            return {'error': 'Trade scenario has no participants.'}, 409

        state = load_state(scenario)

        if verb == 'leave':
            return self._handle_leave(db_session, scenario, merchant, state,
                                      session_factory, current_turn)
        if verb in ('add', 'remove'):
            err = self._handle_basket_edit(db_session, scenario, player,
                                           merchant, state, action, verb)
            if err is not None:
                return err
        elif verb == 'set_currency':
            err = self._handle_currency(state, player, merchant, action)
            if err is not None:
                return err
        elif verb == 'haggle':
            err = self._handle_haggle(db_session, scenario, player, merchant,
                                      state, action, gpt_service)
            if err is not None:
                return err
        elif verb == 'propose':
            return self._handle_propose(db_session, scenario, player,
                                        merchant, state, session_factory,
                                        current_turn)

        save_state(db_session, scenario, state)
        db_session.commit()
        return {
            'view': self.to_view(db_session, scenario),
            'entries': [],
            'resolved': False,
            'summary': '',
        }, 200

    def to_view(self, db_session, scenario):
        roles = participants_by_role(db_session, scenario)
        player = (roles.get('player') or [None])[0]
        merchant = (roles.get('merchant') or [None])[0]
        state = load_state(scenario)
        return {
            'id': scenario.id,
            'kind': self.kind,
            'status': scenario.status,
            'summary': scenario.summary or '',
            'verbs': sorted(TRADE_VERBS),
            'player': self._side_view(db_session, player, state, 'player'),
            'merchant': self._side_view(db_session, merchant, state, 'merchant'),
            'basket_value': self._basket_value(db_session, state),
            'last_haggle': state.get('last_haggle'),
            'log': state.get('log') or [],
        }

    # ----- internals --------------------------------------------------------

    def _basket_key(self, side):
        return 'basket_player' if side == 'player' else 'basket_merchant'

    def _currency_key(self, side):
        return 'currency_player' if side == 'player' else 'currency_merchant'

    def _handle_basket_edit(self, db_session, scenario, player, merchant,
                            state, action, verb):
        side = (action.get('side') or 'player').strip().lower()
        if side not in ('player', 'merchant'):
            return {'error': "side must be 'player' or 'merchant'."}, 400
        owner = player if side == 'player' else merchant
        try:
            ci_id = int(action.get('character_item_id'))
            qty = int(action.get('quantity') or 1)
        except (TypeError, ValueError):
            return {'error': 'character_item_id and quantity are required.'}, 400
        if qty <= 0:
            return {'error': 'quantity must be positive.'}, 400
        ci = (
            db_session.query(CharacterItem)
            .filter(CharacterItem.id == ci_id,
                    CharacterItem.character_id == owner.id)
            .first()
        )
        if ci is None:
            return {'error': "That item isn't held by the chosen side."}, 400

        basket = state.setdefault(self._basket_key(side), {})
        current = int(basket.get(str(ci_id)) or 0)
        owned = int(ci.quantity or 1)
        if verb == 'add':
            new_qty = min(owned, current + qty)
            if new_qty <= 0:
                basket.pop(str(ci_id), None)
            else:
                basket[str(ci_id)] = new_qty
        else:  # remove
            new_qty = max(0, current - qty)
            if new_qty == 0:
                basket.pop(str(ci_id), None)
            else:
                basket[str(ci_id)] = new_qty
        # A basket edit invalidates the previous haggle verdict.
        state['last_haggle'] = None
        return None

    def _handle_currency(self, state, player, merchant, action):
        side = (action.get('side') or 'player').strip().lower()
        if side not in ('player', 'merchant'):
            return {'error': "side must be 'player' or 'merchant'."}, 400
        try:
            amount = max(0, int(action.get('amount') or 0))
        except (TypeError, ValueError):
            return {'error': 'amount must be an integer.'}, 400
        owner = player if side == 'player' else merchant
        wallet = int(owner.current_currency or 0)
        state[self._currency_key(side)] = min(amount, wallet)
        state['last_haggle'] = None
        return None

    def _handle_haggle(self, db_session, scenario, player, merchant, state,
                       action, gpt_service):
        pitch = (action.get('pitch') or '').strip()
        if not pitch:
            return {'error': 'A short pitch is required to haggle.'}, 400
        verdict = self._call_haggle(db_session, player, merchant, state,
                                    pitch, gpt_service)
        state['last_haggle'] = verdict
        log = state.setdefault('log', [])
        log.append({
            'actor': player.name or 'You',
            'verb': 'haggle',
            'text': f'You argue: "{pitch}"',
        })
        log.append({
            'actor': merchant.name or 'Merchant',
            'verb': 'haggle_reply',
            'text': verdict.get('reply') or '',
        })
        return None

    def _handle_propose(self, db_session, scenario, player, merchant, state,
                        session_factory, current_turn):
        verdict = state.get('last_haggle')
        adjustment = int((verdict or {}).get('price_adjustment') or 0)
        accepted = bool((verdict or {}).get('accept'))

        balance = self._trade_balance(db_session, state, adjustment)
        # Without a haggle verdict, accept iff the basket is fair on its own
        # (player side is worth at least as much as the merchant's side after
        # currency contributions). With a verdict, honour it directly.
        if verdict is None:
            accepted = balance >= 0

        if not accepted:
            text = ((verdict or {}).get('reply')
                    or f"{merchant.name or 'The merchant'} shakes their head.")
            entry = transcript_service.add_entry(
                session_factory, scenario.seed_id,
                transcript_service.KIND_DIALOGUE, text,
                turn=current_turn, speaker=merchant.name or 'Merchant',
                meta={'scenario_kind': self.kind, 'verb': 'propose_reject',
                      'scenario_id': scenario.id},
            )
            entries = []
            if entry is not None:
                entries.append({'id': entry.id,
                                'kind': transcript_service.KIND_DIALOGUE,
                                'speaker': merchant.name or 'Merchant',
                                'text': text})
            save_state(db_session, scenario, state)
            db_session.commit()
            return {
                'view': self.to_view(db_session, scenario),
                'entries': entries,
                'resolved': False,
                'summary': '',
            }, 200

        # Accepted: transfer items + currency in both directions, then
        # resolve the scenario.
        moved = self._transfer_baskets(db_session, player, merchant, state)
        self._transfer_currency(db_session, player, merchant, state)
        summary = (f"Trade with {merchant.name or 'merchant'} settled "
                   f"({moved} item line(s) exchanged).")
        resolve(db_session, scenario, 'resolved', summary,
                current_turn=current_turn)
        entry = transcript_service.add_entry(
            session_factory, scenario.seed_id,
            transcript_service.KIND_SYSTEM, summary,
            turn=current_turn, speaker='Narrator',
            meta={'scenario_kind': self.kind, 'verb': 'propose_accept',
                  'scenario_id': scenario.id},
        )
        entries = []
        if entry is not None:
            entries.append({'id': entry.id,
                            'kind': transcript_service.KIND_SYSTEM,
                            'speaker': 'Narrator', 'text': summary})
        save_state(db_session, scenario, state)
        db_session.commit()
        return {
            'view': self.to_view(db_session, scenario),
            'entries': entries,
            'resolved': True,
            'summary': summary,
        }, 200

    def _handle_leave(self, db_session, scenario, merchant, state,
                      session_factory, current_turn):
        summary = f"You walk away from {merchant.name or 'the merchant'}'s wares."
        resolve(db_session, scenario, 'aborted', summary,
                current_turn=current_turn)
        db_session.commit()
        entry = transcript_service.add_entry(
            session_factory, scenario.seed_id,
            transcript_service.KIND_SYSTEM, summary,
            turn=current_turn, speaker='Narrator',
            meta={'scenario_kind': self.kind, 'verb': 'leave',
                  'scenario_id': scenario.id},
        )
        entries = []
        if entry is not None:
            entries.append({'id': entry.id,
                            'kind': transcript_service.KIND_SYSTEM,
                            'speaker': 'Narrator', 'text': summary})
        return {
            'view': self.to_view(db_session, scenario),
            'entries': entries,
            'resolved': True,
            'summary': summary,
        }, 200

    # ----- pricing / transfers ---------------------------------------------

    def _basket_value(self, db_session, state):
        """Return the gross value of each side's basket, currency included."""
        return {
            'player': self._side_value(db_session, state, 'player'),
            'merchant': self._side_value(db_session, state, 'merchant'),
        }

    def _side_value(self, db_session, state, side):
        basket = state.get(self._basket_key(side)) or {}
        currency = int(state.get(self._currency_key(side)) or 0)
        if not basket:
            return currency
        ids = [int(k) for k in basket.keys()]
        rows = (
            db_session.query(CharacterItem)
            .filter(CharacterItem.id.in_(ids))
            .all()
        )
        total = currency
        for ci in rows:
            qty = int(basket.get(str(ci.id)) or 0)
            unit = float((ci.item.value if ci.item else 0) or 0)
            total += int(round(unit * qty))
        return total

    def _trade_balance(self, db_session, state, adjustment_pct):
        """Return player_offer - merchant_offer, after the merchant's margin.

        ``adjustment_pct`` is the merchant's haggle adjustment in percent of
        the merchant-side basket value (positive means they want more from
        the player to seal the deal).
        """
        player_value = self._side_value(db_session, state, 'player')
        merchant_value = self._side_value(db_session, state, 'merchant')
        adjustment = int(round(merchant_value * (adjustment_pct / 100.0)))
        return player_value - (merchant_value + adjustment)

    def _transfer_baskets(self, db_session, player, merchant, state):
        """Move the agreed items between the two characters."""
        moved = 0
        moved += self._move_basket(db_session, state.get('basket_player') or {},
                                   from_owner=player, to_owner=merchant)
        moved += self._move_basket(db_session, state.get('basket_merchant') or {},
                                   from_owner=merchant, to_owner=player)
        return moved

    def _move_basket(self, db_session, basket, *, from_owner, to_owner):
        """Move ``basket`` line items from one character to another.

        Lines that exhaust the source stack are reassigned wholesale; partial
        transfers split the source row into a remainder + a new row owned by
        the receiver. Returns the number of line items processed.
        """
        moved = 0
        for ci_id_str, qty in basket.items():
            qty = int(qty or 0)
            if qty <= 0:
                continue
            ci = (
                db_session.query(CharacterItem)
                .filter(CharacterItem.id == int(ci_id_str),
                        CharacterItem.character_id == from_owner.id)
                .first()
            )
            if ci is None:
                continue
            owned = int(ci.quantity or 1)
            if qty >= owned:
                ci.character_id = to_owner.id
                ci.updated_at = datetime.datetime.now()
                db_session.add(ci)
            else:
                ci.quantity = owned - qty
                ci.updated_at = datetime.datetime.now()
                db_session.add(ci)
                db_session.add(CharacterItem(
                    seed_id=ci.seed_id,
                    character_id=to_owner.id,
                    item_id=ci.item_id,
                    quantity=qty,
                    condition=ci.condition,
                ))
            moved += 1
        db_session.flush()
        return moved

    def _transfer_currency(self, db_session, player, merchant, state):
        cp = int(state.get('currency_player') or 0)
        cm = int(state.get('currency_merchant') or 0)
        if cp:
            player.current_currency = max(0, int(player.current_currency or 0) - cp)
            merchant.current_currency = int(merchant.current_currency or 0) + cp
        if cm:
            merchant.current_currency = max(0, int(merchant.current_currency or 0) - cm)
            player.current_currency = int(player.current_currency or 0) + cm
        if cp or cm:
            db_session.add_all([player, merchant])
            db_session.flush()

    # ----- LLM --------------------------------------------------------------

    def _call_haggle(self, db_session, player, merchant, state, pitch,
                     gpt_service):
        if gpt_service is None:
            return {'accept': False, 'price_adjustment': 0,
                    'reply': "Take it or leave it."}
        rel = self._merchant_relationship(db_session, player, merchant)
        prompt = SCENARIO_PROMPTS['TRADE_HAGGLE'].format(
            merchant_name=merchant.name or 'Merchant',
            player_name=player.name or 'Player',
            merchant_profile=self._format_merchant(merchant),
            relationship=rel,
            basket=self._format_basket_for_prompt(state),
            pitch=pitch,
        )
        try:
            payload = gpt_service.get_structured(
                prompt, TradeHaggleOut, max_attempts=2, temperature=0.7)
        except Exception:
            payload = None
        if payload is None:
            return {'accept': False, 'price_adjustment': 0,
                    'reply': "Hmm. I'll need to think on that."}
        return {
            'accept': bool(payload.accept),
            'price_adjustment': max(-50, min(50, int(payload.price_adjustment))),
            'reply': (payload.reply or '').strip(),
        }

    def _merchant_relationship(self, db_session, player, merchant):
        # Read from the merchant -> player relationship row if available;
        # falls back to a neutral readout when the row hasn't been seeded
        # yet (e.g. first-meet trades on a brand new dynamic NPC).
        row = (
            db_session.query(CharacterRelationship)
            .filter(CharacterRelationship.character_id == merchant.id,
                    CharacterRelationship.related_character_id == player.id)
            .first()
        )
        if row is None:
            return '(neutral; no prior relationship)'
        return (f"attraction: {row.attraction}, respect: {row.respect}, "
                f"trust: {row.trust}, familiarity: {row.familiarity}, "
                f"anger: {row.anger}, fear: {row.fear}")

    def _format_merchant(self, c):
        bits = [f"name: {c.name}"]
        for attr in ('race', 'level', 'charisma', 'intelligence'):
            v = getattr(c, attr, None)
            if v is not None:
                bits.append(f"{attr}: {v}")
        return ', '.join(bits)

    def _format_basket_for_prompt(self, state):
        cp = int(state.get('currency_player') or 0)
        cm = int(state.get('currency_merchant') or 0)
        return (f"player offers {len(state.get('basket_player') or {})} "
                f"item line(s) + {cp} currency; merchant offers "
                f"{len(state.get('basket_merchant') or {})} item line(s) "
                f"+ {cm} currency")

    # ----- view helpers -----------------------------------------------------

    def _side_view(self, db_session, char, state, side):
        if char is None:
            return None
        inventory = (
            db_session.query(CharacterItem)
            .filter(CharacterItem.character_id == char.id)
            .all()
        )
        basket = state.get(self._basket_key(side)) or {}
        return {
            'id': char.id, 'name': char.name, 'race': char.race,
            'level': char.level,
            'currency': int(char.current_currency or 0),
            'currency_offered': int(state.get(self._currency_key(side)) or 0),
            'inventory': [
                {
                    'character_item_id': ci.id,
                    'item_id': ci.item_id,
                    'name': ci.item.name if ci.item else 'Unknown Item',
                    'quantity': int(ci.quantity or 1),
                    'value': float((ci.item.value if ci.item else 0) or 0),
                    'in_basket': int(basket.get(str(ci.id)) or 0),
                }
                for ci in inventory
            ],
        }


handler = TradeHandler()

