import pytest

from app.world_building.world_building import WorldBuilder


@pytest.mark.llm
@pytest.mark.slow
def test_build_world(db_session, openai_client, grok_model, seed_in_db):
    seed_data = {"theme": "cyberpunk"}

    progress_messages = []

    def callback(msg, status='info'):
        progress_messages.append((msg, status))

    world_builder = WorldBuilder(
        seed_data, seed_in_db.id, db_session, openai_client, grok_model, callback
    )

    results = world_builder.build_world()

    expected_keys = {
        'main_character',
        'main_character_skills',
        'main_character_statuses',
        'locations',
        'surrounding_characters',
        'surrounding_characters_skills',
        'surrounding_characters_statuses',
        'surrounding_characters_relationships',
        'surrounding_characters_items',
    }
    assert expected_keys <= set(results.keys())

    # Core stages must succeed for the build to be considered usable.
    assert results['main_character']['status'] == 'success'
    assert results['locations']['status'] == 'success'

    # Locations populated by LocationBuilder are propagated to CharacterBuilder.
    assert world_builder.character_builder.locations == world_builder.location_builder.locations

    # Progress callback is invoked for every stage and ends with a success message.
    assert len(progress_messages) > 0
    assert progress_messages[-1] == ("World building complete!", "success")
