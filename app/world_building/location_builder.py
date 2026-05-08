# location_builder.py
from datetime import datetime
import traceback
from app.orm import Location
from app.prompt_templates import WORLD_BUILDING
from app.world_building.schemas import LocationListOut


class LocationBuilder:
    def __init__(self, seed_data, seed_id, session, gpt_service, progress_callback=None):
        self.seed_data = seed_data
        self.seed_id = seed_id
        self.session = session
        self.gpt_service = gpt_service
        self.progress_callback = progress_callback or (lambda msg, status='info': None)

    def create_locations(self):
        """Generate all locations and their sub-locations in a single batched call."""
        try:
            payload = self.gpt_service.get_structured(
                WORLD_BUILDING['LOCATIONS_BATCH'].format(self.seed_data),
                LocationListOut,
                max_attempts=3,
                temperature=1.2,
            )

            if payload is None:
                return {"message": "Failed to generate location data", "status": "failure"}

            locations = []
            for loc in payload.locations:
                new_location = Location(
                    seed_id=self.seed_id,
                    name=loc.name,
                    description=loc.description,
                    longitude=loc.longitude,
                    latitude=loc.latitude,
                    type=loc.type,
                    climate=loc.climate,
                    terrain=loc.terrain,
                    created_at=datetime.now(),
                    updated_at=datetime.now(),
                )
                self.session.add(new_location)
                self.session.flush()

                for sub in loc.sub_locations:
                    new_sub = Location(
                        seed_id=self.seed_id,
                        name=sub.name,
                        description=sub.description,
                        longitude=sub.longitude,
                        latitude=sub.latitude,
                        type=sub.type,
                        climate=sub.climate,
                        terrain=sub.terrain,
                        parent_id=new_location.id,
                        created_at=datetime.now(),
                        updated_at=datetime.now(),
                    )
                    self.session.add(new_sub)

                locations.append({
                    'id': new_location.id,
                    'name': loc.name,
                    'description': loc.description,
                    'type': loc.type,
                    'climate': loc.climate,
                    'terrain': loc.terrain,
                })

            self.session.commit()
            self.locations = locations
            print("Locations created successfully")
            return {"message": "Locations created successfully", "status": "success"}
        except Exception as e:
            self.session.rollback()
            print(f'Error during location creation: {e}')
            traceback.print_exc()
            return {"message": f"Error during location creation. {e}", "status": "failure"}