# world_builder.py
from app.services.gpt_service import GPTService
from app.world_building.character_builder import CharacterBuilder
from app.world_building.location_builder import LocationBuilder

class WorldBuilder:
    def __init__(self, seed_data, seed_id, session, openai, model):
        self.gpt_service = GPTService(openai, model)
        self.character_builder = CharacterBuilder(seed_data, seed_id, session, self.gpt_service)
        self.location_builder = LocationBuilder(seed_data, seed_id, session, self.gpt_service)

    def build_world(self):
        results = {}
        results['main_character'] = self.character_builder.create_main_character()
        results['main_character_skills'] = self.character_builder.create_main_character_skills()
        results['main_character_statuses'] = self.character_builder.create_main_character_statuses()
        results['locations'] = self.location_builder.create_locations()
        results['surrounding_characters'] = self.character_builder.create_surrounding_characters()
        results['surrounding_characters_skills'] = self.character_builder.create_surrounding_characters_skills()
        results['surrounding_characters_statuses'] = self.character_builder.create_surrounding_characters_statuses()
        results['surrounding_characters_relationships'] = self.character_builder.create_surrounding_characters_relationships()
        results['surrounding_characters_items'] = self.character_builder.create_surrounding_characters_items()
        return results
