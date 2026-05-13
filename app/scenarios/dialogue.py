"""Dialogue scenario: focused conversation with one or more NPCs.

State shape::

    {
        "history": [{"speaker": "<name>", "text": "...", "verb": "say"}, ...],
        "current_npc_id": <int>,           # the NPC the player is addressing
    }

Actions accepted via ``apply_action``::

    {"verb": "say"|"persuade"|"intimidate"|"flirt"|"gift"|"leave",
     "text": "<player line>", "npc_id": <optional int>, "item_id": <opt>}
"""
from __future__ import annotations

import datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from app.orm import (
    Character, CharacterItem, CharacterRelationship, ScenarioParticipant,
)
from app.prompt_templates import SCENARIO_PROMPTS
from app.services import transcript_service

from .base import (
    KIND_DIALOGUE, ScenarioHandler, add_participant, load_state,
    lookup_characters_by_name, participants_by_role, resolve, save_state,
)


# Verbs the frontend may send. Anything else is rejected with an error.
DIALOGUE_VERBS = {'say', 'persuade', 'intimidate', 'flirt', 'gift', 'leave'}

# Verb-specific bias added to whatever the LLM returns, so e.g. flirting
# always nudges attraction up at least a tiny amount even when the model
# omits the key. Keys mirror CharacterRelationship columns.
_VERB_BIAS = {
    'say':        {'familiarity': 1},
    'persuade':   {'familiarity': 1, 'respect': 1},
    'intimidate': {'familiarity': 1, 'fear': 2, 'trust': -1},
    'flirt':      {'familiarity': 1, 'attraction': 1},
    'gift':       {'familiarity': 1, 'trust': 1, 'attraction': 1},
}

_RELATIONSHIP_FIELDS = ('attraction', 'respect', 'trust', 'familiarity',
                        'anger', 'fear')


class DialogueReplyOut(BaseModel):
    """LLM payload for a single NPC reply turn."""
    reply: str = ""
    mood: str = ""
    deltas: dict = Field(default_factory=dict)


class DialogueHandler(ScenarioHandler):
    kind = KIND_DIALOGUE

    # ----- substrate hooks --------------------------------------------------

    def start(self, db_session, seed_id, trigger, *, current_turn=None,
              session_factory=None, gpt_service=None):
        from app.orm import Scenario  # local import to avoid cycles
        npcs = lookup_characters_by_name(db_session, seed_id, trigger.participants)
        if not npcs:
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
        for idx, npc in enumerate(npcs, start=1):
            add_participant(db_session, scenario, npc.id, 'npc', idx)

        save_state(db_session, scenario, {
            'history': [],
            'current_npc_id': npcs[0].id,
        })
        db_session.commit()
        db_session.refresh(scenario)
        return scenario

    def apply_action(self, db_session, scenario, action, *,
                     gpt_service=None, session_factory=None,
                     current_turn=None):
        verb = (action.get('verb') or '').strip().lower()
        if verb not in DIALOGUE_VERBS:
            return {'error': f'Unknown dialogue verb: {verb}'}, 400

        roles = participants_by_role(db_session, scenario)
        player = (roles.get('player') or [None])[0]
        npcs = roles.get('npc') or []
        if player is None or not npcs:
            return {'error': 'Dialogue scenario has no participants.'}, 409

        state = load_state(scenario)
        target = self._select_target(npcs, action.get('npc_id'),
                                     state.get('current_npc_id'))
        state['current_npc_id'] = target.id

        if verb == 'leave':
            return self._handle_leave(db_session, scenario, player, target,
                                      session_factory, current_turn)

        text = (action.get('text') or '').strip()
        return self._handle_speech(
            db_session, scenario, player, target, npcs, verb, text,
            action.get('item_id'), state, gpt_service, session_factory,
            current_turn,
        )

    def to_view(self, db_session, scenario):
        roles = participants_by_role(db_session, scenario)
        player = (roles.get('player') or [None])[0]
        npcs = roles.get('npc') or []
        state = load_state(scenario)
        current_id = state.get('current_npc_id') or (npcs[0].id if npcs else None)

        return {
            'id': scenario.id,
            'kind': self.kind,
            'status': scenario.status,
            'summary': scenario.summary or '',
            'verbs': sorted(DIALOGUE_VERBS),
            'player': self._character_view(db_session, player) if player else None,
            'npcs': [
                self._npc_view(db_session, scenario, npc, current=npc.id == current_id)
                for npc in npcs
            ],
            'current_npc_id': current_id,
            'history': state.get('history') or [],
        }

    # ----- internals --------------------------------------------------------

    def _select_target(self, npcs, requested_id, fallback_id):
        """Resolve the NPC the player is addressing this turn."""
        if requested_id is not None:
            try:
                rid = int(requested_id)
            except (TypeError, ValueError):
                rid = None
            if rid is not None:
                for npc in npcs:
                    if npc.id == rid:
                        return npc
        if fallback_id is not None:
            for npc in npcs:
                if npc.id == fallback_id:
                    return npc
        return npcs[0]

    def _handle_leave(self, db_session, scenario, player, target,
                      session_factory, current_turn):
        summary = f"You ended the conversation with {target.name}."
        resolve(db_session, scenario, 'resolved', summary,
                current_turn=current_turn)
        db_session.commit()
        entry = transcript_service.add_entry(
            session_factory, scenario.seed_id,
            transcript_service.KIND_DIALOGUE, summary,
            turn=current_turn, speaker='Narrator',
            meta={'scenario_kind': self.kind, 'verb': 'leave'},
        )
        entries = []
        if entry is not None:
            entries.append({'id': entry.id, 'kind': transcript_service.KIND_DIALOGUE,
                            'speaker': 'Narrator', 'text': summary})
        return {
            'view': self.to_view(db_session, scenario),
            'entries': entries,
            'resolved': True,
            'summary': summary,
        }, 200

    def _handle_speech(self, db_session, scenario, player, target, npcs, verb,
                       text, item_id, state, gpt_service, session_factory,
                       current_turn):
        if verb == 'gift':
            ok, gift_summary = self._transfer_gift(db_session, player, target, item_id)
            if not ok:
                return {'error': gift_summary}, 400
            text = text or gift_summary

        if not text:
            return {'error': 'A line of dialogue is required.'}, 400

        history = state.setdefault('history', [])
        history.append({'speaker': player.name or 'You', 'text': text, 'verb': verb})
        entries = []
        player_entry = transcript_service.add_entry(
            session_factory, scenario.seed_id,
            transcript_service.KIND_DIALOGUE, text,
            turn=current_turn, speaker=player.name or 'You',
            meta={'scenario_kind': self.kind, 'verb': verb,
                  'scenario_id': scenario.id},
        )
        if player_entry is not None:
            entries.append({'id': player_entry.id,
                            'kind': transcript_service.KIND_DIALOGUE,
                            'speaker': player.name or 'You', 'text': text})

        reply, deltas = self._call_npc_reply(db_session, scenario, player,
                                             target, verb, text, history,
                                             gpt_service)
        merged = self._merge_deltas(verb, deltas)
        self._apply_relationship_deltas(db_session, scenario.seed_id, target,
                                        player, merged)

        npc_speaker = target.name or 'NPC'
        history.append({'speaker': npc_speaker, 'text': reply, 'verb': 'reply'})
        npc_entry = transcript_service.add_entry(
            session_factory, scenario.seed_id,
            transcript_service.KIND_DIALOGUE, reply,
            turn=current_turn, speaker=npc_speaker,
            meta={'scenario_kind': self.kind, 'verb': 'reply',
                  'scenario_id': scenario.id, 'deltas': merged},
        )
        if npc_entry is not None:
            entries.append({'id': npc_entry.id,
                            'kind': transcript_service.KIND_DIALOGUE,
                            'speaker': npc_speaker, 'text': reply})

        save_state(db_session, scenario, state)
        db_session.commit()
        return {
            'view': self.to_view(db_session, scenario),
            'entries': entries,
            'resolved': False,
            'summary': '',
            'deltas': merged,
        }, 200

    def _transfer_gift(self, db_session, player, target, item_id):
        if item_id is None:
            return False, 'Pick an item to give.'
        try:
            iid = int(item_id)
        except (TypeError, ValueError):
            return False, 'Invalid item id.'
        ci = (
            db_session.query(CharacterItem)
            .filter(CharacterItem.id == iid,
                    CharacterItem.character_id == player.id)
            .first()
        )
        if ci is None:
            return False, "You don't carry that item."
        ci.character_id = target.id
        ci.updated_at = datetime.datetime.now()
        db_session.add(ci)
        db_session.flush()
        item_name = ci.item.name if ci.item else 'a gift'
        return True, f"You hand over {item_name}."

    def _call_npc_reply(self, db_session, scenario, player, target, verb,
                        text, history, gpt_service):
        """Ask the LLM for the NPC's reply + relationship deltas.

        Falls back to a deterministic placeholder reply when no GPT service
        is supplied (the substrate's offline path) or the call fails, so a
        flaky LLM doesn't strand the player mid-dialogue.
        """
        if gpt_service is None:
            return self._fallback_reply(verb, target), {}

        rel = self._npc_relationship_to_player(db_session, scenario.seed_id,
                                               target, player)
        prompt = SCENARIO_PROMPTS['DIALOGUE_REPLY'].format(
            npc_name=target.name or 'NPC',
            player_name=player.name or 'Player',
            verb=verb,
            player_line=text,
            npc_profile=self._format_npc_profile(target),
            relationship=self._format_relationship(rel),
            history=self._format_history(history[-12:-1]),  # exclude current line
        )
        try:
            payload = gpt_service.get_structured(
                prompt, DialogueReplyOut, max_attempts=2, temperature=0.9,
            )
        except Exception:
            payload = None
        if payload is None or not (payload.reply or '').strip():
            return self._fallback_reply(verb, target), {}
        return payload.reply.strip(), dict(payload.deltas or {})

    def _merge_deltas(self, verb, llm_deltas):
        """Combine the verb's baseline bias with whatever the LLM returned."""
        merged = dict(_VERB_BIAS.get(verb, {}))
        for k, v in (llm_deltas or {}).items():
            if k not in _RELATIONSHIP_FIELDS:
                continue
            try:
                v = int(v)
            except (TypeError, ValueError):
                continue
            merged[k] = max(-3, min(3, merged.get(k, 0) + v))
        return merged

    def _apply_relationship_deltas(self, db_session, seed_id, npc, player,
                                   deltas):
        """Update the NPC -> player relationship row in place."""
        if not deltas:
            return
        rel = self._npc_relationship_to_player(db_session, seed_id, npc, player)
        if rel is None:
            rel = CharacterRelationship(
                seed_id=seed_id, character_id=npc.id,
                related_character_id=player.id,
                relationship_type='acquaintance',
                attraction=5, respect=5, trust=5,
                familiarity=1, anger=5, fear=5,
            )
            db_session.add(rel)
            db_session.flush()
        for key, delta in deltas.items():
            current = getattr(rel, key, None)
            if current is None:
                continue
            setattr(rel, key, max(0, min(10, int(current) + int(delta))))
        rel.updated_at = datetime.datetime.now()
        db_session.add(rel)
        db_session.flush()

    def _npc_relationship_to_player(self, db_session, seed_id, npc, player):
        return (
            db_session.query(CharacterRelationship)
            .filter(CharacterRelationship.seed_id == seed_id,
                    CharacterRelationship.character_id == npc.id,
                    CharacterRelationship.related_character_id == player.id)
            .first()
        )

    def _format_npc_profile(self, npc):
        bits = [f"name: {npc.name}"]
        if npc.race: bits.append(f"race: {npc.race}")
        if npc.level is not None: bits.append(f"level: {npc.level}")
        return ', '.join(bits)

    def _format_relationship(self, rel):
        if rel is None:
            return '(no prior relationship; treat as a stranger)'
        return ', '.join(f"{k}: {getattr(rel, k)}" for k in _RELATIONSHIP_FIELDS)

    def _format_history(self, history):
        if not history:
            return '(no prior lines)'
        return '\n'.join(f"  {h.get('speaker', '?')}: {h.get('text', '')}"
                         for h in history)

    def _fallback_reply(self, verb, npc):
        canned = {
            'say':        "I hear you.",
            'persuade':   "Hm. You make a fair point.",
            'intimidate': "Don't push your luck, stranger.",
            'flirt':      "Mind your tongue.",
            'gift':       "Generous of you. I won't forget it.",
        }
        return canned.get(verb, "...")

    def _character_view(self, db_session, char):
        return {
            'id': char.id, 'name': char.name, 'race': char.race,
            'level': char.level,
        }

    def _npc_view(self, db_session, scenario, npc, *, current=False):
        roles = participants_by_role(db_session, scenario)
        player = (roles.get('player') or [None])[0]
        rel = self._npc_relationship_to_player(db_session, scenario.seed_id,
                                               npc, player) if player else None
        rel_view = None
        if rel is not None:
            rel_view = {f: getattr(rel, f) for f in _RELATIONSHIP_FIELDS}
            rel_view['relationship_type'] = rel.relationship_type
        return {
            'id': npc.id, 'name': npc.name, 'race': npc.race,
            'level': npc.level, 'current': bool(current),
            'relationship': rel_view,
        }


handler = DialogueHandler()

