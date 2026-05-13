"""Battle scenario: deterministic, turn-based 1v1 combat.

State shape::

    {
        "round": <int>,
        "turn_order": [<character_id>, ...],
        "active_index": <int>,         # index into turn_order
        "hp": {<character_id>: <int>}, # current HP per participant
        "max_hp": {<character_id>: <int>},
        "guarding": {<character_id>: <bool>},  # halves next incoming attack
        "log": [{"actor": <name>, "verb": "...", "text": "..."}, ...],
    }

Verbs accepted via ``apply_action``::

    {"verb": "attack" | "defend" | "flee"}

The combat math is intentionally deterministic so the scenario plays the
same with or without an LLM; the LLM is only consulted for the NPC's
choice of verb and a one-line flavour string. If the LLM is missing or
fails, the NPC falls back to a stat-driven heuristic.
"""
from __future__ import annotations

import random
from typing import List, Optional

from pydantic import BaseModel, Field

from app.orm import Character
from app.prompt_templates import SCENARIO_PROMPTS
from app.services import transcript_service

from .base import (
    KIND_BATTLE, ScenarioHandler, add_participant, load_state,
    lookup_characters_by_name, participants_by_role, resolve, save_state,
)


BATTLE_VERBS = {'attack', 'defend', 'flee'}


class BattleNPCActionOut(BaseModel):
    """LLM payload picking the NPC's next verb + a flavour line."""
    verb: str = 'attack'
    flavour: str = ''


class BattleHandler(ScenarioHandler):
    kind = KIND_BATTLE

    # ----- substrate hooks --------------------------------------------------

    def start(self, db_session, seed_id, trigger, *, current_turn=None,
              session_factory=None):
        from app.orm import Scenario
        opponents = lookup_characters_by_name(db_session, seed_id,
                                              trigger.participants)
        if not opponents:
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
        # Single opponent for now: extra participants are dropped to keep
        # the initiative loop simple. The substrate is happy to enrol more
        # later when a multi-target battle UI ships.
        opp = opponents[0]
        add_participant(db_session, scenario, opp.id, 'opponent', 1)

        order = self._initiative_order(mc, opp)
        save_state(db_session, scenario, {
            'round': 1,
            'turn_order': [c.id for c in order],
            'active_index': 0,
            'hp': {str(mc.id): mc.current_health or 0,
                   str(opp.id): opp.current_health or 0},
            'max_hp': {str(mc.id): mc.max_health or (mc.current_health or 1),
                       str(opp.id): opp.max_health or (opp.current_health or 1)},
            'guarding': {str(mc.id): False, str(opp.id): False},
            'log': [],
        })
        db_session.commit()
        db_session.refresh(scenario)
        return scenario

    def apply_action(self, db_session, scenario, action, *,
                     gpt_service=None, session_factory=None,
                     current_turn=None):
        verb = (action.get('verb') or '').strip().lower()
        if verb not in BATTLE_VERBS:
            return {'error': f'Unknown battle verb: {verb}'}, 400

        roles = participants_by_role(db_session, scenario)
        player = (roles.get('player') or [None])[0]
        opp = (roles.get('opponent') or [None])[0]
        if player is None or opp is None:
            return {'error': 'Battle scenario has no participants.'}, 409

        state = load_state(scenario)
        if not self._is_player_turn(state, player.id):
            return {'error': "It's not your turn."}, 409

        entries = []
        # 1) Player acts.
        self._apply_verb(state, actor=player, target=opp, verb=verb)
        entries.append(self._log_to_transcript(
            session_factory, scenario, state['log'][-1], current_turn))

        resolved, summary = self._check_resolution(state, player, opp)
        # 2) Opponent acts (if still alive AND player didn't flee).
        if not resolved:
            self._advance_turn(state)
            opp_verb, opp_flavour = self._npc_action(
                db_session, scenario, opp, player, state, gpt_service)
            self._apply_verb(state, actor=opp, target=player, verb=opp_verb,
                             flavour=opp_flavour)
            entries.append(self._log_to_transcript(
                session_factory, scenario, state['log'][-1], current_turn))
            resolved, summary = self._check_resolution(state, player, opp)
            self._advance_turn(state)
            if state['active_index'] == 0:
                state['round'] = (state.get('round') or 1) + 1

        # Persist HP back to the Character rows so the rest of the game sees
        # the damage even if the scenario is aborted later.
        self._persist_hp(db_session, [player, opp], state)
        save_state(db_session, scenario, state)

        if resolved:
            resolve(db_session, scenario, 'resolved', summary,
                    current_turn=current_turn)
        db_session.commit()

        return {
            'view': self.to_view(db_session, scenario),
            'entries': [e for e in entries if e is not None],
            'resolved': resolved,
            'summary': summary if resolved else '',
        }, 200

    def to_view(self, db_session, scenario):
        roles = participants_by_role(db_session, scenario)
        player = (roles.get('player') or [None])[0]
        opp = (roles.get('opponent') or [None])[0]
        state = load_state(scenario)
        return {
            'id': scenario.id,
            'kind': self.kind,
            'status': scenario.status,
            'summary': scenario.summary or '',
            'verbs': sorted(BATTLE_VERBS),
            'round': state.get('round') or 1,
            'active_id': self._active_character_id(state),
            'log': state.get('log') or [],
            'player': self._combatant_view(player, state) if player else None,
            'opponent': self._combatant_view(opp, state) if opp else None,
        }

    # ----- internals --------------------------------------------------------

    def _initiative_order(self, mc, opp):
        """Higher speed acts first; ties broken by agility, then random."""
        def key(c):
            return (-(c.speed or 5), -(c.agility or 5), random.random())
        return sorted([mc, opp], key=key)

    def _is_player_turn(self, state, player_id):
        active = self._active_character_id(state)
        return active == player_id

    def _active_character_id(self, state):
        order = state.get('turn_order') or []
        idx = state.get('active_index') or 0
        if not order:
            return None
        return order[idx % len(order)]

    def _advance_turn(self, state):
        order = state.get('turn_order') or []
        if not order:
            return
        state['active_index'] = ((state.get('active_index') or 0) + 1) % len(order)

    def _apply_verb(self, state, *, actor, target, verb, flavour=''):
        """Apply ``verb`` to the state and append a log entry."""
        log = state.setdefault('log', [])
        guarding = state.setdefault('guarding', {})
        hp = state.setdefault('hp', {})
        akey, tkey = str(actor.id), str(target.id)

        if verb == 'attack':
            # Clear actor's own guard before swinging; defending only affects
            # the next incoming attack on the actor, not their own.
            guarding[akey] = False
            damage = self._roll_damage(actor, target)
            if guarding.get(tkey):
                damage = max(1, damage // 2)
                guarding[tkey] = False
            hp[tkey] = max(0, (hp.get(tkey) or 0) - damage)
            text = (flavour or
                    f"{actor.name or 'Attacker'} strikes "
                    f"{target.name or 'the target'} for {damage} damage.")
            log.append({'actor': actor.name or '?', 'verb': 'attack',
                        'damage': damage, 'text': text})
        elif verb == 'defend':
            guarding[akey] = True
            text = (flavour or
                    f"{actor.name or 'Defender'} braces for the next blow.")
            log.append({'actor': actor.name or '?', 'verb': 'defend',
                        'text': text})
        elif verb == 'flee':
            guarding[akey] = False
            state['_fled_by'] = actor.id
            text = (flavour or
                    f"{actor.name or 'Combatant'} breaks off and flees.")
            log.append({'actor': actor.name or '?', 'verb': 'flee',
                        'text': text})

    def _roll_damage(self, actor, target):
        """Stat-driven damage roll: STR + d6, minus a slice of target AGI."""
        base = (actor.strength or 5) + random.randint(1, 6)
        soak = (target.agility or 5) // 3
        return max(1, base - soak)

    def _check_resolution(self, state, player, opp):
        hp = state.get('hp') or {}
        fled = state.get('_fled_by')
        if fled == player.id:
            return True, f"You fled the fight with {opp.name or 'your opponent'}."
        if fled == opp.id:
            return True, f"{opp.name or 'Your opponent'} broke off and fled."
        if (hp.get(str(opp.id)) or 0) <= 0:
            return True, f"You defeated {opp.name or 'your opponent'}."
        if (hp.get(str(player.id)) or 0) <= 0:
            return True, f"You were defeated by {opp.name or 'your opponent'}."
        return False, ''

    def _persist_hp(self, db_session, characters, state):
        """Write the post-action HP back onto each Character row."""
        hp = state.get('hp') or {}
        for c in characters:
            new_hp = hp.get(str(c.id))
            if new_hp is None:
                continue
            c.current_health = int(new_hp)
            db_session.add(c)
        db_session.flush()

    def _npc_action(self, db_session, scenario, npc, player, state, gpt_service):
        """Pick a verb + flavour for ``npc`` (LLM with heuristic fallback)."""
        npc_hp = (state.get('hp') or {}).get(str(npc.id), 0)
        npc_max = (state.get('max_hp') or {}).get(str(npc.id), 1) or 1
        ratio = npc_hp / npc_max if npc_max else 0
        if gpt_service is not None:
            prompt = SCENARIO_PROMPTS['BATTLE_NPC_ACTION'].format(
                npc_name=npc.name or 'NPC',
                player_name=player.name or 'Player',
                npc_profile=self._format_combatant(npc),
                player_profile=self._format_combatant(player),
                npc_hp=npc_hp, npc_max_hp=npc_max,
                player_hp=(state.get('hp') or {}).get(str(player.id), 0),
                player_max_hp=(state.get('max_hp') or {}).get(str(player.id), 1),
                history=self._format_log(state.get('log') or []),
            )
            try:
                payload = gpt_service.get_structured(
                    prompt, BattleNPCActionOut, max_attempts=2, temperature=0.5)
            except Exception:
                payload = None
            if payload is not None:
                verb = (payload.verb or '').strip().lower()
                if verb in BATTLE_VERBS:
                    return verb, (payload.flavour or '').strip()
        # Heuristic fallback.
        if ratio < 0.2:
            return 'flee', ''
        if ratio < 0.4:
            return 'defend', ''
        return 'attack', ''

    def _format_combatant(self, c):
        bits = [f"name: {c.name}"]
        for attr in ('race', 'level', 'strength', 'agility', 'speed'):
            v = getattr(c, attr, None)
            if v is not None:
                bits.append(f"{attr}: {v}")
        return ', '.join(bits)

    def _format_log(self, log):
        if not log:
            return '(no prior actions)'
        return '\n'.join(f"  {row.get('actor', '?')}: {row.get('text', '')}"
                         for row in log[-8:])

    def _log_to_transcript(self, session_factory, scenario, log_entry,
                           current_turn):
        text = log_entry.get('text') or ''
        if not text:
            return None
        speaker = log_entry.get('actor') or 'Combat'
        entry = transcript_service.add_entry(
            session_factory, scenario.seed_id,
            transcript_service.KIND_COMBAT, text,
            turn=current_turn, speaker=speaker,
            meta={'scenario_kind': self.kind, 'verb': log_entry.get('verb'),
                  'scenario_id': scenario.id},
        )
        if entry is None:
            return None
        return {'id': entry.id, 'kind': transcript_service.KIND_COMBAT,
                'speaker': speaker, 'text': text}

    def _combatant_view(self, c, state):
        hp = (state.get('hp') or {}).get(str(c.id), 0)
        max_hp = (state.get('max_hp') or {}).get(str(c.id), 1) or 1
        return {
            'id': c.id, 'name': c.name, 'race': c.race, 'level': c.level,
            'hp': hp, 'max_hp': max_hp,
            'guarding': bool((state.get('guarding') or {}).get(str(c.id))),
        }


handler = BattleHandler()

