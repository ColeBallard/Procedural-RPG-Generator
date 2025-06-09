# location_builder.py
from datetime import datetime
import traceback
from app.orm import Location
from app.prompt_templates import WORLD_BUILDING

class LocationBuilder:
    def __init__(self, seed_data, seed_id, session, gpt_service):
        self.seed_data = seed_data
        self.seed_id = seed_id
        self.session = session
        self.gpt_service = gpt_service

    def create_locations(self):
        retries = 0
        max_retries = 5
        while True:
            try:
                location_text = self.get_gpt_response(WORLD_BUILDING['LOCATIONS'].format(self.seed_data))

                # Parse the generated location data
                locations = self.extract_json(location_text, list_flag=True)
                if locations is None:
                    return {"message": "Failed to generate location data", "status": "failure"}

                for loc in locations:
                    new_location = Location(
                        seed_id=self.seed_id,
                        name=loc['name'],
                        description=loc['description'],
                        longitude=loc.get('longitude'),
                        latitude=loc.get('latitude'),
                        type=loc.get('type'),
                        climate=loc.get('climate'),
                        terrain=loc.get('terrain'),
                        created_at=datetime.now(),
                        updated_at=datetime.now()
                    )
                    self.session.add(new_location)
                    self.session.commit()

                    loc['id'] = new_location.id

                    sub_retries = 0
                    max_sub_retries = 5

                    while True:
                        try:
                            sub_location_text = self.get_gpt_response(WORLD_BUILDING['SUB_LOCATIONS'].format(loc, self.seed_data))

                            sub_locations = self.extract_json(sub_location_text, list_flag=True)

                            for sub_loc in sub_locations:
                                new_sub_location = Location(
                                    seed_id=self.seed_id,
                                    name=sub_loc['name'],
                                    description=sub_loc['description'],
                                    longitude=sub_loc.get('longitude'),
                                    latitude=sub_loc.get('latitude'),
                                    type=sub_loc.get('type'),
                                    climate=sub_loc.get('climate'),
                                    terrain=sub_loc.get('terrain'),
                                    parent_id=new_location.id,
                                    created_at=datetime.now(),
                                    updated_at=datetime.now()
                                )
                                self.session.add(new_sub_location)

                            self.session.commit()
                            print('Sub locations created successfully.')
                            break
          
                        except Exception as e:
                            sub_retries += 1
                            self.session.rollback()
                            if sub_retries > max_sub_retries:
                                print(f'Exceeded max try limit for sub locations. Location {loc['name']} will have no sub-locations.')
                                break
                            else:
                                print(f'Error occured for sub location world building. Retrying attempt {sub_retries}/{max_sub_retries}. {traceback.print_exc()}.')

                
                self.locations = locations
                print(self.locations)

                print("Locations created successfully")
                return {"message": "Locations created successfully", "status": "success"}
            except Exception as e:
                retries += 1
                self.session.rollback()
                if retries > max_retries:
                    print(f'Exceeded max try limit for locations. Unable to properly world build.')
                    return {"message": f"Error during location creation. {traceback.print_exc()}", "status": "failure"}
                else:
                    print(f'Error occurred for location world building.')
                    print(f'Retrying attempt {retries}/{max_retries}. Error: {traceback.print_exc()}.')