# world_builder.py
from app.services.gpt_service import GPTService
from app.world_building.character_builder import CharacterBuilder
from app.world_building.location_builder import LocationBuilder

class WorldBuilder:
    def __init__(self, seed_data, seed_id, session, openai, model, progress_callback=None):
        self.gpt_service = GPTService(openai, model)
        self.progress_callback = progress_callback or (lambda msg, status='info': None)
        self.character_builder = CharacterBuilder(seed_data, seed_id, session, self.gpt_service, self.progress_callback)
        self.location_builder = LocationBuilder(seed_data, seed_id, session, self.gpt_service, self.progress_callback)

    def build_world(self):
        results = {}

        self.progress_callback("Creating main character...")
        results['main_character'] = self.character_builder.create_main_character()

        self.progress_callback("Creating main character skills...")
        results['main_character_skills'] = self.character_builder.create_main_character_skills()

        self.progress_callback("Creating main character statuses...")
        results['main_character_statuses'] = self.character_builder.create_main_character_statuses()

        self.progress_callback("Creating locations...")
        results['locations'] = self.location_builder.create_locations()

        # Pass locations to character builder
        self.character_builder.locations = self.location_builder.locations

        self.progress_callback("Creating surrounding characters...")
        results['surrounding_characters'] = self.character_builder.create_surrounding_characters()

        self.progress_callback("Creating surrounding characters skills...")
        results['surrounding_characters_skills'] = self.character_builder.create_surrounding_characters_skills()

        self.progress_callback("Creating surrounding characters statuses...")
        results['surrounding_characters_statuses'] = self.character_builder.create_surrounding_characters_statuses()

        self.progress_callback("Creating surrounding characters relationships...")
        results['surrounding_characters_relationships'] = self.character_builder.create_surrounding_characters_relationships()

        self.progress_callback("Creating surrounding characters items...")
        results['surrounding_characters_items'] = self.character_builder.create_surrounding_characters_items()

        self.progress_callback("World building complete!", "success")
        return results
