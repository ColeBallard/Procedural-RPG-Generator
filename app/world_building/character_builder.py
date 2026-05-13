# character_builder.py
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
import json
import random
import traceback

from app.orm import (
    Character, Skill, CharacterSkill, Status, CharacterStatus,
    Event, EventCharacter, CharacterRelationship, Item, CharacterItem, Seed,
)
from app.prompt_templates import WORLD_BUILDING
from app.services import elevenlabs_service
from app.world_building.schemas import (
    EventOut, MainCharacterOut, MainCharacterItemsOut, NPCListOut, RelationshipOut,
)


class CharacterBuilder:
    # Bound on concurrent LLM calls so we don't blow past xAI rate limits.
    _MAX_WORKERS = 8

    def __init__(self, seed_data, seed_id, session, gpt_service,
                 progress_callback=None, name_service=None,
                 elevenlabs_api_key=None):
        self.seed_data = seed_data
        self.seed_id = seed_id
        self.session = session
        self.gpt_service = gpt_service
        self.name_service = name_service
        # Optional: when present, characters get an ElevenLabs voice_id
        # assigned via voice search at creation time. Missing/invalid keys
        # are tolerated -- voice_id simply stays NULL and the TTS layer
        # falls back to the narrator voice at playback time.
        self.elevenlabs_api_key = elevenlabs_api_key
        self.progress_callback = progress_callback or (lambda msg, status='info': None)

    def _pick_voice_id(self, *, gender, date_of_birth, race, search_text=None):
        """Resolve a voice id for a character, or None if EL is unavailable."""
        if not self.elevenlabs_api_key:
            return None
        current_dt = None
        if hasattr(self, 'character_data'):
            current_dt = self.character_data.get('current_date_time')
        try:
            return elevenlabs_service.find_voice_for_character(
                self.elevenlabs_api_key,
                gender=gender,
                date_of_birth=date_of_birth,
                current_dt=current_dt,
                race=race,
                search_text=search_text,
            )
        except Exception as e:
            print(f'Voice search failed: {e}')
            return None

    def _seeded_name_or_none(self, gender, category='first'):
        """Pull a name from NameLibrary using the seed's persisted themes."""
        if self.name_service is None:
            return None
        themes = self.name_service.get_themes_for_seed(self.seed_id)
        if not themes:
            return None
        return self.name_service.random_name(themes, gender=gender, category=category)

    @staticmethod
    def _seed_data_name(seed_data):
        """Return the user-provided main-character name, or None.

        ``seed_data`` arrives as a dict from in-process callers and as a JSON
        string from the HTTP layer (the frontend ``JSON.stringify``s the form
        payload before posting), so accept both shapes.
        """
        if isinstance(seed_data, str):
            try:
                seed_data = json.loads(seed_data)
            except (ValueError, TypeError):
                return None
        if not isinstance(seed_data, dict):
            return None
        for key in ('character_name', 'name', 'main_character_name'):
            v = seed_data.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
        return None

    @classmethod
    def _seed_data_has_name(cls, seed_data):
        """Detect a user-provided main-character name on the incoming seed."""
        return cls._seed_data_name(seed_data) is not None

    def create_main_character(self):
        """Single batched call: core stats + skills + statuses in one LLM round-trip."""
        payload = self.gpt_service.get_structured(
            WORLD_BUILDING['MAIN_CHARACTER_BATCH'].format(self.seed_data),
            MainCharacterOut,
            max_attempts=3,
            temperature=1.1,
        )

        if payload is None:
            print("No valid JSON data was extracted.")
            return {"message": "Failed to create main character due to invalid data",
                    "status": "failure"}

        try:
            stats = {s: random.randint(8, 16) for s in
                     ('strength', 'speed', 'agility', 'intelligence', 'wisdom', 'charisma')}

            # Prefer the user-supplied name; otherwise override the LLM name
            # with one drawn from the seed's chosen naming themes.
            user_name = self._seed_data_name(self.seed_data)
            if user_name:
                name = user_name
            else:
                name = payload.name
                seeded = self._seeded_name_or_none(payload.gender, category='first')
                if seeded:
                    name = seeded

            voice_id = self._pick_voice_id(
                gender=payload.gender,
                date_of_birth=payload.date_of_birth,
                race=payload.race,
            )

            new_character = Character(
                seed_id=self.seed_id,
                main_character=1,
                alive=1,
                name=name,
                date_of_birth=payload.date_of_birth,
                race=payload.race,
                gender=payload.gender,
                level=1,
                exp_points=0,
                created_at=datetime.now(),
                updated_at=datetime.now(),
                current_health=100,
                max_health=100,
                current_currency=0,
                voice_id=voice_id,
                **stats,
            )
            self.session.add(new_character)
            self.session.flush()

            if payload.current_date_time:
                seed = self.session.query(Seed).filter(Seed.id == self.seed_id).one()
                seed.current_date_time = payload.current_date_time

            for skill in payload.skills:
                new_skill = Skill(name=skill.name, description=skill.description,
                                  created_at=datetime.now(), updated_at=datetime.now())
                self.session.add(new_skill)
                self.session.flush()
                self.session.add(CharacterSkill(
                    seed_id=self.seed_id, character_id=new_character.id,
                    skill_id=new_skill.id, level=1, exp_points=0,
                    created_at=datetime.now(), updated_at=datetime.now(),
                ))

            for st in payload.statuses:
                new_status = Status(name=st.name, description=st.description, type=st.type,
                                    duration=st.duration, created_at=datetime.now(),
                                    updated_at=datetime.now())
                self.session.add(new_status)
                self.session.flush()
                self.session.add(CharacterStatus(
                    seed_id=self.seed_id, character_id=new_character.id,
                    status_id=new_status.id, active=False,
                    end_date_time=datetime.now(), created_at=datetime.now(),
                    updated_at=datetime.now(),
                ))

            self.session.commit()

            self.character_data = {
                'id': new_character.id,
                'name': name,
                'race': payload.race,
                'gender': payload.gender,
                'date_of_birth': payload.date_of_birth,
                'current_date_time': payload.current_date_time,
                'skills': [s.model_dump() for s in payload.skills],
                'statuses': [s.model_dump() for s in payload.statuses],
                **stats,
            }

            print('Main character created successfully')
            return {"message": "Main character created successfully", "status": "success"}
        except Exception as e:
            self.session.rollback()
            traceback.print_exc()
            return {"message": f"Failed to create main character. {e}", "status": "failure"}

    def create_main_character_skills(self):
        """No-op: skills are batched into create_main_character. Preserved for
        backwards compatibility with the legacy WorldBuilder result schema."""
        count = len(self.character_data.get('skills', [])) if hasattr(self, 'character_data') else 0
        return {"message": f"Main character skills batched ({count}).", "status": "success"}

    def create_main_character_statuses(self):
        """No-op: statuses are batched into create_main_character."""
        count = len(self.character_data.get('statuses', [])) if hasattr(self, 'character_data') else 0
        return {"message": f"Main character statuses batched ({count}).", "status": "success"}

    def create_main_character_items(self):
        """Give the protagonist a small starter inventory via one LLM call.

        The prompt constrains the LLM to 2-4 mundane, low-power items with
        capped value/weight so a fresh run never opens with overpowered gear.
        Failures here are non-fatal: the MC simply starts empty-handed.
        """
        mc_payload = getattr(self, 'character_data', None) or {}
        mc_id = mc_payload.get('id')
        if not mc_id:
            return {"message": "Main character not created; skipping starter items.",
                    "status": "skipped"}

        payload = self.gpt_service.get_structured(
            WORLD_BUILDING['MAIN_CHARACTER_ITEMS_BATCH'].format(
                character=mc_payload, seed_data=self.seed_data),
            MainCharacterItemsOut,
            max_attempts=2,
            temperature=0.9,
        )
        if payload is None or not payload.items:
            return {"message": "No starter items generated.", "status": "success"}

        persisted = 0
        for item in payload.items:
            try:
                new_item = Item(name=item.name, description=item.description,
                                type=item.type, value=item.value, weight=item.weight,
                                created_at=datetime.now(), updated_at=datetime.now())
                self.session.add(new_item)
                self.session.flush()
                self.session.add(CharacterItem(
                    seed_id=self.seed_id, character_id=mc_id,
                    item_id=new_item.id, quantity=item.quantity,
                    condition=item.condition,
                    created_at=datetime.now(), updated_at=datetime.now(),
                ))
                persisted += 1
            except Exception as e:
                self.session.rollback()
                print(f"Failed to persist starter item '{getattr(item, 'name', '?')}': {e}")

        try:
            self.session.commit()
        except Exception as e:
            self.session.rollback()
            traceback.print_exc()
            return {"message": f"Failed to commit starter items. {e}",
                    "status": "failure"}

        self.character_data['items'] = [i.model_dump() for i in payload.items]
        return {"message": f"Main character starter items created ({persisted}).",
                "status": "success"}

    # ------------------------------------------------------------------ #
    # Surrounding characters (NPCs)                                       #
    # ------------------------------------------------------------------ #
    def create_surrounding_characters(self):
        """Generate NPCs for every location in parallel using a thread pool.

        Each location triggers ONE batched LLM call that returns the NPC core
        data plus event, skills, statuses, and items. Workers only do LLM
        work; database writes happen on the orchestrating thread.
        """
        if not getattr(self, 'locations', None):
            self.NPCs_data = []
            return {"message": "No locations available; nothing to populate.",
                    "status": "success"}

        npcs_per_location = {}
        with ThreadPoolExecutor(max_workers=self._MAX_WORKERS) as pool:
            futures = {
                pool.submit(self._fetch_npcs_for_location, loc): loc
                for loc in self.locations
            }
            for future in as_completed(futures):
                loc = futures[future]
                try:
                    npcs_per_location[loc['id']] = future.result() or []
                except Exception as e:
                    print(f"NPC fetch failed for location {loc.get('name')}: {e}")
                    npcs_per_location[loc['id']] = []

        # Flatten the per-location buckets so the voice-search pass can run
        # one future per NPC across the whole world. Persistence is done on
        # this thread (SQLAlchemy session is not safe to share), but voice
        # search is pure HTTP and parallelises freely.
        flat_npcs = [
            (loc, npc)
            for loc in self.locations
            for npc in npcs_per_location.get(loc['id'], [])
        ]
        voice_ids = self._batch_resolve_npc_voices([npc for _, npc in flat_npcs])

        all_npcs_data = []
        for idx, (location, npc) in enumerate(flat_npcs):
            try:
                persisted = self._persist_npc(npc, location, voice_id=voice_ids[idx])
                if persisted is not None:
                    all_npcs_data.append(persisted)
            except Exception as e:
                self.session.rollback()
                print(f"Failed to persist NPC '{getattr(npc, 'name', '?')}' "
                      f"in {location.get('name')}: {e}")
                continue

        self.NPCs_data = all_npcs_data
        print(f"Surrounding characters created: {len(all_npcs_data)} NPCs")
        return {"message": "Surrounding characters and their events created successfully",
                "status": "success"}

    def _fetch_npcs_for_location(self, location):
        payload = self.gpt_service.get_structured(
            WORLD_BUILDING['NPCS_FOR_LOCATION_BATCH'].format(location, self.seed_data),
            NPCListOut,
            max_attempts=2,
            temperature=1.1,
        )
        return list(payload.npcs) if payload else []

    def _batch_resolve_npc_voices(self, npcs):
        """Run all NPC voice searches concurrently and return them in order.

        Returns a list of voice ids (or ``None``) the same length as
        ``npcs``. When no ElevenLabs key is configured the list is filled
        with ``None`` without spawning any workers.
        """
        n = len(npcs)
        if n == 0:
            return []
        if not self.elevenlabs_api_key:
            return [None] * n

        results = [None] * n

        def _resolve(idx, npc):
            event_hint = getattr(getattr(npc, 'event', None), 'description', None)
            return idx, self._pick_voice_id(
                gender=npc.gender,
                date_of_birth=npc.date_of_birth,
                race=npc.race,
                search_text=event_hint,
            )

        with ThreadPoolExecutor(max_workers=self._MAX_WORKERS) as pool:
            futures = [pool.submit(_resolve, i, npc) for i, npc in enumerate(npcs)]
            for future in as_completed(futures):
                try:
                    idx, vid = future.result()
                    results[idx] = vid
                except Exception as e:
                    print(f'NPC voice search failed: {e}')
        return results

    def _persist_npc(self, npc, location, voice_id=None):
        level = random.randint(1, 3)

        # Override the LLM-generated NPC name with one drawn from the seed's
        # naming themes whenever the library has a match. NPCs always defer
        # to the library since the user only ever pre-names the protagonist.
        seeded = self._seeded_name_or_none(npc.gender, category='first')
        npc_name = seeded if seeded else npc.name

        new_character = Character(
            seed_id=self.seed_id,
            main_character=False,
            alive=True,
            name=npc_name,
            date_of_birth=npc.date_of_birth,
            race=npc.race,
            gender=npc.gender,
            level=level,
            exp_points=100 * ((2 ** (level - 1)) - 1),
            created_at=datetime.now(),
            updated_at=datetime.now(),
            strength=random.randint(4, 16) + level,
            speed=random.randint(4, 16) + level,
            agility=random.randint(4, 16) + level,
            intelligence=random.randint(4, 16) + level,
            wisdom=random.randint(4, 16) + level,
            charisma=random.randint(4, 16) + level,
            current_health=100 * level,
            max_health=100 * level,
            current_currency=random.randint(0, 1000),
            voice_id=voice_id,
        )
        self.session.add(new_character)
        self.session.flush()

        current_dt = self.character_data.get('current_date_time') if hasattr(self, 'character_data') else None
        new_event = Event(
            seed_id=self.seed_id,
            name=npc.event.name,
            description=npc.event.description,
            start_date_time=current_dt - timedelta(hours=random.randint(1, 5)) if current_dt else None,
            end_date_time=current_dt,
            type=npc.event.type,
            location_id=location['id'],
            start_turn=1,
            end_turn=1,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        self.session.add(new_event)
        self.session.flush()

        self.session.add(EventCharacter(
            seed_id=self.seed_id,
            character_id=new_character.id,
            event_id=new_event.id,
            role=npc.event.role,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        ))

        for skill in npc.skills:
            new_skill = Skill(name=skill.name, description=skill.description,
                              created_at=datetime.now(), updated_at=datetime.now())
            self.session.add(new_skill)
            self.session.flush()
            self.session.add(CharacterSkill(
                seed_id=self.seed_id, character_id=new_character.id,
                skill_id=new_skill.id,
                level=random.randint(1, 5), exp_points=random.randint(0, 100),
                created_at=datetime.now(), updated_at=datetime.now(),
            ))

        for st in npc.statuses:
            new_status = Status(name=st.name, description=st.description, type=st.type,
                                duration=st.duration, created_at=datetime.now(),
                                updated_at=datetime.now())
            self.session.add(new_status)
            self.session.flush()
            self.session.add(CharacterStatus(
                seed_id=self.seed_id, character_id=new_character.id,
                status_id=new_status.id, active=True,
                end_date_time=datetime.now() + timedelta(seconds=st.duration or 0),
                created_at=datetime.now(), updated_at=datetime.now(),
            ))

        for item in npc.items:
            new_item = Item(name=item.name, description=item.description, type=item.type,
                            value=item.value, weight=item.weight,
                            created_at=datetime.now(), updated_at=datetime.now())
            self.session.add(new_item)
            self.session.flush()
            self.session.add(CharacterItem(
                seed_id=self.seed_id, character_id=new_character.id,
                item_id=new_item.id, quantity=item.quantity, condition=item.condition,
                created_at=datetime.now(), updated_at=datetime.now(),
            ))

        self.session.commit()
        return {'id': new_character.id, 'name': npc_name,
                'location_id': location['id']}

    def create_surrounding_characters_skills(self):
        """No-op: skills are batched into create_surrounding_characters."""
        return {"message": "Surrounding character skills batched.", "status": "success"}

    def create_surrounding_characters_statuses(self):
        """No-op: statuses are batched into create_surrounding_characters."""
        return {"message": "Surrounding character statuses batched.", "status": "success"}

    def create_surrounding_characters_items(self):
        """No-op: items are batched into create_surrounding_characters."""
        return {"message": "Surrounding character items batched.", "status": "success"}

    # ------------------------------------------------------------------ #
    # Relationships (parallelized across pairs)                           #
    # ------------------------------------------------------------------ #
    def create_surrounding_characters_relationships(self):
        if not getattr(self, 'NPCs_data', None) or len(self.NPCs_data) < 2:
            return {"message": "No NPC data available to form relationships.",
                    "status": "success"}

        all_pairs = [(i, j) for i in range(len(self.NPCs_data))
                     for j in range(i + 1, len(self.NPCs_data))]
        num_pairs = min(len(all_pairs), 10)
        random_pairs = random.sample(all_pairs, num_pairs)

        results = {}
        with ThreadPoolExecutor(max_workers=self._MAX_WORKERS) as pool:
            futures = {pool.submit(self._fetch_relationship,
                                   self.NPCs_data[i], self.NPCs_data[j]): (i, j)
                       for (i, j) in random_pairs}
            for future in as_completed(futures):
                pair = futures[future]
                try:
                    results[pair] = future.result()
                except Exception as e:
                    print(f"Relationship fetch failed for pair {pair}: {e}")
                    results[pair] = None

        persisted = 0
        for (i, j), rel in results.items():
            if rel is None:
                continue
            try:
                self.session.add(CharacterRelationship(
                    seed_id=self.seed_id,
                    character_id=self.NPCs_data[i]['id'],
                    related_character_id=self.NPCs_data[j]['id'],
                    relationship_type=rel.type,
                    attraction=rel.attraction, respect=rel.respect, trust=rel.trust,
                    familiarity=rel.familiarity, anger=rel.anger, fear=rel.fear,
                    created_at=datetime.now(), updated_at=datetime.now(),
                ))
                persisted += 1
            except Exception as e:
                self.session.rollback()
                print(f"Failed to persist relationship for pair ({i},{j}): {e}")

        try:
            self.session.commit()
        except Exception as e:
            self.session.rollback()
            traceback.print_exc()
            return {"message": f"Failed to commit NPC relationships. {e}",
                    "status": "failure"}

        return {"message": f"NPC relationships established ({persisted}).",
                "status": "success"}

    def _fetch_relationship(self, character, related_character):
        return self.gpt_service.get_structured(
            WORLD_BUILDING['RELATIONSHIP_BATCH'].format(
                character, related_character, self.seed_data),
            RelationshipOut,
            max_attempts=2,
            temperature=0.7,
        )

    # ------------------------------------------------------------------ #
    # Main-character acquaintances                                        #
    # ------------------------------------------------------------------ #
    def create_main_character_relationships(self):
        """Seed a small set of MC <-> NPC acquaintances at world-start.

        Picks 1-3 NPCs weighted toward the protagonist's starting location so
        the MC begins the game knowing only a handful of locals rather than
        the entire populated world. NPCs with no row keyed off the MC are
        treated as strangers (familiarity == 0) by the read path.
        """
        if not getattr(self, 'NPCs_data', None):
            return {"message": "No NPCs available; no MC relationships to form.",
                    "status": "success"}

        mc_payload = getattr(self, 'character_data', None) or {}
        mc_id = mc_payload.get('id')
        if not mc_id:
            return {"message": "Main character not created; skipping MC relationships.",
                    "status": "skipped"}

        indices = self._pick_mc_acquaintance_indices()
        if not indices:
            return {"message": "No MC acquaintances selected.", "status": "success"}

        results = {}
        with ThreadPoolExecutor(max_workers=self._MAX_WORKERS) as pool:
            futures = {pool.submit(self._fetch_relationship,
                                   mc_payload, self.NPCs_data[i]): i
                       for i in indices}
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    results[idx] = future.result()
                except Exception as e:
                    print(f"MC relationship fetch failed for NPC index {idx}: {e}")
                    results[idx] = None

        persisted = 0
        for idx, rel in results.items():
            if rel is None:
                continue
            # Clamp familiarity so a seeded MC acquaintance is never read back
            # as an "unknown" stranger (familiarity == 0 is reserved for that).
            familiarity = max(rel.familiarity or 0, 1)
            try:
                self.session.add(CharacterRelationship(
                    seed_id=self.seed_id,
                    character_id=mc_id,
                    related_character_id=self.NPCs_data[idx]['id'],
                    relationship_type=rel.type,
                    attraction=rel.attraction, respect=rel.respect, trust=rel.trust,
                    familiarity=familiarity, anger=rel.anger, fear=rel.fear,
                    created_at=datetime.now(), updated_at=datetime.now(),
                ))
                persisted += 1
            except Exception as e:
                self.session.rollback()
                print(f"Failed to persist MC relationship for NPC index {idx}: {e}")

        try:
            self.session.commit()
        except Exception as e:
            self.session.rollback()
            traceback.print_exc()
            return {"message": f"Failed to commit MC relationships. {e}",
                    "status": "failure"}

        return {"message": f"Main-character relationships established ({persisted}).",
                "status": "success"}

    def _pick_mc_acquaintance_indices(self):
        """Choose up to three NPCs the MC starts the game knowing.

        Prefers NPCs at the protagonist's starting location (locations[0],
        matching the ordering used by the intro narration), then optionally
        adds one "old contact" drawn from anywhere else at low probability.
        """
        locations = list(getattr(self, 'locations', []) or [])
        starting_loc_id = locations[0]['id'] if locations else None

        local_indices = [i for i, npc in enumerate(self.NPCs_data)
                         if npc.get('location_id') == starting_loc_id]
        other_indices = [i for i, npc in enumerate(self.NPCs_data)
                         if npc.get('location_id') != starting_loc_id]

        target = min(len(local_indices), random.randint(1, 3))
        chosen = random.sample(local_indices, target) if target else []

        if other_indices and random.random() < 0.25:
            chosen.append(random.choice(other_indices))

        return chosen

    # ------------------------------------------------------------------ #
    # Opening event (MC-anchored)                                         #
    # ------------------------------------------------------------------ #
    def create_opening_event(self):
        """Generate one opening event involving the MC at the starting location.

        Persists an Event row plus an EventCharacter linking the protagonist
        so the MC-centric info-panel filters surface at least one event (and
        its location) from world-build time. Failures here are non-fatal:
        the world is already fully persisted; the opening event is a UX
        sweetener on top.
        """
        mc_payload = getattr(self, 'character_data', None) or {}
        mc_id = mc_payload.get('id')
        if not mc_id:
            return {"message": "Main character not created; skipping opening event.",
                    "status": "skipped"}

        locations = list(getattr(self, 'locations', []) or [])
        if not locations:
            return {"message": "No locations available; skipping opening event.",
                    "status": "skipped"}
        starting_location = locations[0]

        payload = self.gpt_service.get_structured(
            WORLD_BUILDING['OPENING_EVENT'].format(
                seed_data=self.seed_data,
                character=mc_payload,
                starting_location=starting_location,
            ),
            EventOut,
            max_attempts=2,
            temperature=0.9,
        )
        if payload is None:
            return {"message": "Failed to generate opening event.",
                    "status": "failure"}

        try:
            current_dt = mc_payload.get('current_date_time')
            new_event = Event(
                seed_id=self.seed_id,
                name=payload.name,
                description=payload.description,
                start_date_time=current_dt - timedelta(hours=random.randint(1, 5)) if current_dt else None,
                end_date_time=current_dt,
                type=payload.type,
                location_id=starting_location['id'],
                start_turn=1,
                end_turn=1,
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )
            self.session.add(new_event)
            self.session.flush()

            self.session.add(EventCharacter(
                seed_id=self.seed_id,
                character_id=mc_id,
                event_id=new_event.id,
                role=payload.role,
                created_at=datetime.now(),
                updated_at=datetime.now(),
            ))
            self.session.commit()
        except Exception as e:
            self.session.rollback()
            traceback.print_exc()
            return {"message": f"Failed to persist opening event. {e}",
                    "status": "failure"}

        # Expose the persisted event so the intro narrator can anchor the
        # opening prose to it; otherwise the narration drifts and the player
        # sees a calm scene while the events panel shows a crisis.
        self.opening_event = {
            'id': new_event.id,
            'name': payload.name,
            'description': payload.description,
            'type': payload.type,
            'role': payload.role,
            'location_id': starting_location['id'],
            'location_name': starting_location.get('name'),
        }

        return {"message": "Opening event created successfully",
                "status": "success",
                "event_id": new_event.id}

