# world_builder.py
import traceback

from app.prompt_templates import WORLD_BUILDING
from app.services.gpt_service import GPTService
from app.services.name_service import NameService
from app.world_building.character_builder import CharacterBuilder
from app.world_building.location_builder import LocationBuilder


class WorldBuilder:
    """Orchestrates the world-building pipeline.

    Heavy LLM concurrency lives inside the builders themselves:
      * ``LocationBuilder`` collapses locations + sub-locations into one call.
      * ``CharacterBuilder`` batches per-NPC LLM work (core + event + skills +
        statuses + items) and parallelizes both the per-location NPC fetch and
        the per-pair relationship fetch via ``ThreadPoolExecutor``.

    The orchestrator keeps the original result-key contract so the SSE route
    and existing tests remain unchanged.
    """

    def __init__(self, seed_data, seed_id, session, openai, model, progress_callback=None):
        self.seed_data = seed_data
        self.seed_id = seed_id
        self.session = session
        self.gpt_service = GPTService(openai, model)
        self.progress_callback = progress_callback or (lambda msg, status='info': None)
        self.name_service = NameService(session, self.gpt_service)
        self.character_builder = CharacterBuilder(
            seed_data, seed_id, session, self.gpt_service, self.progress_callback,
            name_service=self.name_service)
        self.location_builder = LocationBuilder(
            seed_data, seed_id, session, self.gpt_service, self.progress_callback)

    def build_world(self):
        results = {}

        # Phase 0: pick the naming aesthetic ONCE per seed and persist it.
        # Subsequent character lookups (main + NPCs) draw names from the
        # NameLibrary subset matching this choice. If the table is empty or
        # the LLM returns nothing usable, character names fall back to the
        # LLM-generated values.
        self.progress_callback("Selecting theme for the world...")
        chosen = self.name_service.select_themes_for_seed(self.seed_data)
        self.name_service.assign_themes_to_seed(self.seed_id, chosen)
        results['naming_themes'] = {
            "themes": chosen,
            "status": "success" if chosen else "skipped",
            "message": (f"Selected {len(chosen)} naming theme(s)." if chosen
                        else "No naming themes selected; using LLM names."),
        }

        # Phase 1 (sequential to keep the shared SQLAlchemy session safe):
        # main character (now a single batched LLM call) + locations (also a
        # single batched call returning every sub-location).
        self.progress_callback("Creating main character...")
        results['main_character'] = self.character_builder.create_main_character()
        results['main_character_skills'] = self.character_builder.create_main_character_skills()
        results['main_character_statuses'] = self.character_builder.create_main_character_statuses()

        # Hand the protagonist a small, deliberately low-power starter kit so
        # the opening scene has something to interact with without trivializing
        # early encounters. The prompt caps quantity/value/weight; see
        # WORLD_BUILDING['MAIN_CHARACTER_ITEMS_BATCH'].
        self.progress_callback("Equipping main character with starter items...")
        results['main_character_items'] = self.character_builder.create_main_character_items()

        self.progress_callback("Creating locations...")
        results['locations'] = self.location_builder.create_locations()

        self.character_builder.locations = self.location_builder.locations

        # Phase 2: NPC generation runs in parallel across locations internally.
        self.progress_callback("Creating surrounding characters...")
        results['surrounding_characters'] = self.character_builder.create_surrounding_characters()
        results['surrounding_characters_skills'] = self.character_builder.create_surrounding_characters_skills()
        results['surrounding_characters_statuses'] = self.character_builder.create_surrounding_characters_statuses()
        results['surrounding_characters_items'] = self.character_builder.create_surrounding_characters_items()

        # Phase 3: relationships run in parallel across pairs internally.
        self.progress_callback("Creating surrounding characters relationships...")
        results['surrounding_characters_relationships'] = (
            self.character_builder.create_surrounding_characters_relationships())

        # Phase 3b: seed a small handful of MC <-> NPC acquaintances so the
        # protagonist starts the game knowing only a few locals; everyone
        # else is rendered as an unknown stranger by the read path.
        self.progress_callback("Creating main character relationships...")
        results['main_character_relationships'] = (
            self.character_builder.create_main_character_relationships())

        # Phase 4: a single narrator-style opening passage that introduces the
        # world, the protagonist and the starting scene. Persisted by the
        # caller as a 'narration' transcript entry so it survives reloads.
        self.progress_callback("Composing story opening...")
        results['intro_narration'] = self._create_intro_narration()

        self.progress_callback("World building complete!", "success")
        return results

    def _create_intro_narration(self):
        """Produce a short second-person opening passage for the new world.

        Returns the narration text on success, or ``None`` if the LLM call
        fails. A failure here must not abort world-building -- the world is
        already fully persisted; the opening is a UX layer on top of it.
        """
        try:
            character = getattr(self.character_builder, 'character_data', None) or {}
            locations = list(getattr(self.location_builder, 'locations', []) or [])
            if not locations:
                return None

            starting_location = locations[0]
            other_locations = locations[1:]

            prompt = WORLD_BUILDING['INTRO_NARRATIVE'].format(
                seed_data=self.seed_data,
                character=character,
                starting_location=starting_location,
                other_locations=other_locations,
            )
            text = self.gpt_service.get_response(prompt, temperature=1.0)
            return text.strip() if text else None
        except Exception as e:
            print(f'Intro narration generation failed: {e}')
            traceback.print_exc()
            return None
