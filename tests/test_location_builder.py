import pytest
from unittest.mock import MagicMock

from app.world_building.location_builder import LocationBuilder


@pytest.fixture
def seed_data():
    return {"theme": "fantasy"}


@pytest.mark.llm
def test_create_locations_success(seed_data, db_session, gpt_service, seed_in_db):
    builder = LocationBuilder(seed_data, seed_in_db.id, db_session, gpt_service)

    result = builder.create_locations()

    assert result["status"] == "success"
    assert result["message"] == "Locations created successfully"
    assert len(builder.locations) >= 1
    for loc in builder.locations:
        assert loc.get("name")
        assert loc.get("description")
        assert "id" in loc


def test_create_locations_json_extraction_failure(seed_data, db_session, seed_in_db):
    # Failure-path unit test: stub get_structured to simulate exhausted retries.
    bad_service = MagicMock()
    bad_service.get_structured.return_value = None

    builder = LocationBuilder(seed_data, seed_in_db.id, db_session, bad_service)

    result = builder.create_locations()

    assert result["status"] == "failure"
    assert result["message"] == "Failed to generate location data"
