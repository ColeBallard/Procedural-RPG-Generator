# character_builder.py
from datetime import datetime, timedelta
import random
import traceback
from app.orm import Character, Skill, CharacterSkill, Status, CharacterStatus, Event, EventCharacter, CharacterRelationship, Item, CharacterItem, Seed
from app.prompt_templates import WORLD_BUILDING

class CharacterBuilder:
    def __init__(self, seed_data, seed_id, session, gpt_service):
        self.seed_data = seed_data
        self.seed_id = seed_id
        self.session = session
        self.gpt_service = gpt_service

    def create_main_character(self):
        retries = 0
        max_retries = 5
        while True:
            character_text = self.get_gpt_response(WORLD_BUILDING['MAIN_CHARACTER'].format(self.seed_data))

            character_data = self.extract_json(character_text)

            if character_data is None:
                print("No valid JSON data was extracted.")
                return {"message": "Failed to create main character due to invalid data", "status": "failure"}

            # Adding random attributes if they are not set
            stats = ['strength', 'speed', 'agility', 'intelligence', 'wisdom', 'charisma']
            for stat in stats:
                if stat not in character_data or character_data[stat] is None:
                    character_data[stat] = random.randint(8, 16)  # Ensure all stats have a value

            try:
                new_character = Character(
                    seed_id=self.seed_id,
                    main_character=1,  # True as this is the main character
                    alive=1,  # Initially alive
                    name=character_data['name'],
                    date_of_birth=character_data['date_of_birth'],
                    race=character_data['race'],
                    gender=character_data['gender'],
                    level=1,  # Starts at level 1
                    exp_points=0,  # No experience points initially
                    created_at=datetime.now(),
                    updated_at=datetime.now(),
                    strength=character_data['strength'],
                    speed=character_data['speed'],
                    agility=character_data['agility'],
                    intelligence=character_data['intelligence'],
                    wisdom=character_data['wisdom'],
                    charisma=character_data['charisma'],
                    current_health=100,
                    max_health=100,
                    current_currency=0  # No currency initially
                )
                self.session.add(new_character)
                self.session.commit()

                if 'current_date_time' in character_data:
                    # Fetch the seed record by seed_id
                    seed = self.session.query(Seed).filter(Seed.id == self.seed_id).one()
                    
                    # Update the current_date_time
                    seed.current_date_time = character_data['current_date_time']

                    # Commit the changes to the database
                    self.session.commit()

                self.character_data = character_data
                self.character_data['id'] = new_character.id

                print('Main character created successfully')
                return {"message": "Main character created successfully", "status": "success"}
            except Exception as e:
                retries += 1
                self.session.rollback()
                if retries > max_retries:
                    print(f'Exceeded max try limit for main character. Unable to properly world build.')
                    return {"message": f"Failed to create main character. {traceback.print_exc()}", "status": "failure"}
                else:
                    print(f'Error occured for main character world building. Retrying attempt {retries}/{max_retries}. {traceback.print_exc()}.')

    def create_main_character_skills(self):
        retries = 0
        max_retries = 5

        while retries <= max_retries:
            skills_text = self.get_gpt_response(WORLD_BUILDING['MAIN_CHARACTER_SKILLS'].format(self.character_data, self.seed_data))
            skills_data = self.extract_json(skills_text, list_flag=True)

            if skills_data is None:
                print("No valid JSON data was extracted for skills.")
                retries += 1
                continue

            try:
                for skill in skills_data:
                    # Create a new Skill instance
                    new_skill = Skill(
                        name=skill['name'],
                        description=skill['description'],
                        created_at=datetime.now(),
                        updated_at=datetime.now()
                    )
                    self.session.add(new_skill)
                    self.session.flush()  # Flush to get the skill_id before committing

                    # Create a CharacterSkill instance linking the character to the new skill
                    new_character_skill = CharacterSkill(
                        seed_id=self.seed_id,
                        character_id=self.character_data['id'],  # Assume character_data has the character's id
                        skill_id=new_skill.id,
                        level=1,  # Randomly assign a level or based on some logic
                        exp_points=0,
                        created_at=datetime.now(),
                        updated_at=datetime.now()
                    )
                    self.session.add(new_character_skill)

                    

                self.character_data['skills'] = skills_data

                self.session.commit()

                print("Main character skills created successfully")
                return {"message": "Main character skills created successfully", "status": "success"}

            except Exception as e:
                self.session.rollback()
                retries += 1
                if retries > max_retries:
                    print(f'Exceeded max try limit for main character skills. Unable to properly world build.')
                    return {"message": f"Failed to create main character skills. {traceback.print_exc()}", "status": "failure"}
                else:
                    print(f'Error occurred for main character skills world building. Retrying attempt {retries}/{max_retries}. {traceback.print_exc()}')

        return {"message": "Failed to create main character skills after multiple attempts.", "status": "failure"}
    
    def create_main_character_statuses(self):
        retries = 0
        max_retries = 5

        while retries <= max_retries:
            statuses_text = self.get_gpt_response(WORLD_BUILDING['MAIN_CHARACTER_STATUSES'].format(self.character_data, self.seed_data))
            statuses_data = self.extract_json(statuses_text, list_flag=True)

            if statuses_data is None:
                print("No valid JSON data was extracted for statuses.")
                retries += 1
                continue

            try:
                for status in statuses_data:
                    # Create a new Status instance
                    new_status = Status(
                        name=status['name'],
                        description=status['description'],
                        type=status['type'],
                        duration=status.get('duration', 0),  # Default duration to 0 if not specified
                        created_at=datetime.now(),
                        updated_at=datetime.now()
                    )
                    self.session.add(new_status)
                    self.session.flush()  # Flush to get the status_id before committing

                    # Create a CharacterStatus instance linking the character to the new status
                    new_character_status = CharacterStatus(
                        seed_id=self.seed_id,
                        character_id=self.character_data['id'],  # Assume character_data has the character's id
                        status_id=new_status.id,
                        active=False,  # Assume the status is initially active
                        end_date_time=datetime.now(),  # Calculate end time if applicable
                        created_at=datetime.now(),
                        updated_at=datetime.now()
                    )
                    self.session.add(new_character_status)

                    

                self.character_data['statuses'] = statuses_data  # Store for any further processing

                self.session.commit()

                print("Main character statuses created successfully")
                return {"message": "Main character statuses created successfully", "status": "success"}

            except Exception as e:
                self.session.rollback()
                retries += 1
                if retries > max_retries:
                    print(f'Exceeded max try limit for main character statuses. Unable to properly world build.')
                    return {"message": f"Failed to create main character statuses. {traceback.print_exc()}", "status": "failure"}
                else:
                    print(f'Error occurred for main character statuses world building. Retrying attempt {retries}/{max_retries}. {traceback.print_exc()}')

        return {"message": "Failed to create main character statuses after multiple attempts.", "status": "failure"}
    
    def create_surrounding_characters(self):
        retries = 0
        max_retries = 5
        while retries <= max_retries:
            try:
                for location in self.locations:
                    characters_text = self.get_gpt_response(WORLD_BUILDING['SURROUNDING_CHARACTERS'].format(location, self.seed_data))
                    characters_data = self.extract_json(characters_text, list_flag=True)

                    if characters_data is None:
                        print("No valid JSON data was extracted for surrounding characters.")
                        retries += 1
                        continue

                    for character in characters_data:
                        level = random.randint(1, 3)
                                             
                        new_character = Character(
                            seed_id=self.seed_id,
                            main_character=False,
                            alive=True,
                            name=character['name'],
                            date_of_birth=character['date_of_birth'],
                            race=character['race'],
                            gender=character['gender'],
                            level=level,
                            exp_points=100*((2**(level-1))-1),
                            created_at=datetime.now(),
                            updated_at=datetime.now(),
                            strength=random.randint(4, 16)+level,
                            speed=random.randint(4, 16)+level,
                            agility=random.randint(4, 16)+level,
                            intelligence=random.randint(4, 16)+level,
                            wisdom=random.randint(4, 16)+level,
                            charisma=random.randint(4, 16)+level,
                            current_health=100*level,
                            max_health=100*level,
                            current_currency=random.randint(0, 1000)
                        )
                        self.session.add(new_character)
                        self.session.flush()  # Flush to ensure new_character.id is available

                        print('Created surrounding player.')

                        character['id'] = new_character.id

                        # Generate and assign events to the new character
                        event_text = self.get_gpt_response(WORLD_BUILDING['CHARACTER_EVENT'].format(character, location, self.seed_data))
                        event_data = self.extract_json(event_text, list_flag=False, nested_key='event')

                        new_event = Event(
                            seed_id=self.seed_id,
                            name=event_data['name'],
                            description=event_data['description'],
                            start_date_time=self.character_data['current_date_time'] - timedelta(hours=random.randint(1, 5)) if self.character_data['current_date_time'] else None,
                            end_date_time=self.character_data['current_date_time'] if self.character_data['current_date_time'] else None,
                            type=event_data['type'],
                            location_id=location['id'],
                            start_turn=1,
                            end_turn=1,
                            created_at=datetime.now(),
                            updated_at=datetime.now()
                        )
                        self.session.add(new_event)
                        self.session.flush()  # Flush to ensure new_event.id is available

                        new_event_character = EventCharacter(
                            seed_id=self.seed_id,
                            character_id=new_character.id,
                            event_id=new_event.id,
                            role=event_data['role'],
                            created_at=datetime.now(),
                            updated_at=datetime.now()
                        )
                        self.session.add(new_event_character)
                    
                        self.session.commit()  # Commit after processing each location

                        print('Created surrounding player event.')

                self.NPCs_data = characters_data
                
                print("Surrounding characters and their events created successfully")
                return {"message": "Surrounding characters and their events created successfully", "status": "success"}
            except Exception as e:
                self.session.rollback()
                retries += 1
                if retries > max_retries:
                    print(f'Exceeded max try limit for creating surrounding characters. {traceback.print_exc()}')
                    return {"message": "Failed to create surrounding characters. Retry limit exceeded.", "status": "failure"}
                else:
                    print(f'Error occurred while creating surrounding characters. Retrying... {traceback.print_exc()}')
        return {"message": "Failed to create surrounding characters after multiple attempts.", "status": "failure"}

    def create_surrounding_characters_skills(self):
        retries = 0
        max_retries = 5
        if not hasattr(self, 'NPCs_data') or not self.NPCs_data:
            return {"message": "No surrounding characters data available.", "status": "failure"}

        while retries <= max_retries:
            try:
                for npc in self.NPCs_data:
                    skills_text = self.get_gpt_response(WORLD_BUILDING['CHARACTER_SKILLS'].format(npc, self.seed_data))
                    skills_data = self.extract_json(skills_text, list_flag=True)

                    if skills_data is None:
                        print(f"No valid JSON data was extracted for skills of character {npc['name']}.")
                        continue

                    for skill in skills_data:
                        new_skill = Skill(
                            name=skill['name'],
                            description=skill['description'],
                            created_at=datetime.now(),
                            updated_at=datetime.now()
                        )
                        self.session.add(new_skill)
                        self.session.flush()  # Flush to get the skill_id before committing

                        new_character_skill = CharacterSkill(
                            seed_id=self.seed_id,
                            character_id=npc['id'],  # Use the stored character ID from self.NPCs_data
                            skill_id=new_skill.id,
                            level=random.randint(1, 5),  # Example: Randomly assign a skill level
                            exp_points=random.randint(0, 100),
                            created_at=datetime.now(),
                            updated_at=datetime.now()
                        )
                        self.session.add(new_character_skill)

                        

                    self.session.commit()
                    print(f"Skills created successfully for character {npc['name']}")

                    

                return {"message": "Skills for all surrounding characters created successfully", "status": "success"}

            except Exception as e:
                self.session.rollback()
                retries += 1
                if retries > max_retries:
                    print(f'Exceeded max try limit for creating surrounding character skills. {traceback.print_exc()}')
                    return {"message": "Failed to create surrounding character skills. Retry limit exceeded.", "status": "failure"}
                else:
                    print(f'Error occurred while creating surrounding character skills. Retrying... {traceback.print_exc()}')
        
        return {"message": "Failed to create surrounding character skills after multiple attempts.", "status": "failure"}

    def create_surrounding_characters_statuses(self):
        retries = 0
        max_retries = 5
        if not hasattr(self, 'NPCs_data') or not self.NPCs_data:
            return {"message": "No surrounding characters data available.", "status": "failure"}

        while retries <= max_retries:
            try:
                for npc in self.NPCs_data:
                    statuses_text = self.get_gpt_response(WORLD_BUILDING['CHARACTER_STATUSES'].format(npc, self.seed_data))
                    statuses_data = self.extract_json(statuses_text, list_flag=True)

                    if statuses_data is None:
                        print(f"No valid JSON data was extracted for statuses of character {npc['name']}.")
                        continue

                    for status in statuses_data:
                        new_status = Status(
                            name=status['name'],
                            description=status['description'],
                            type=status['type'],
                            duration=status.get('duration', 0),  # Default duration to 0 if not specified
                            created_at=datetime.now(),
                            updated_at=datetime.now()
                        )
                        self.session.add(new_status)
                        self.session.flush()  # Flush to get the status_id before committing

                        new_character_status = CharacterStatus(
                            seed_id=self.seed_id,
                            character_id=npc['id'],  # Use the stored NPC ID from self.NPCs_data
                            status_id=new_status.id,
                            active=True,  # Assume the status is initially active
                            end_date_time=datetime.now() + timedelta(hours=status.get('duration', 0)),  # Calculate end time if applicable
                            created_at=datetime.now(),
                            updated_at=datetime.now()
                        )
                        self.session.add(new_character_status)

                        

                    self.session.commit()
                    print(f"Statuses created successfully for character {npc['name']}")

                    

                return {"message": "Statuses for all surrounding characters created successfully", "status": "success"}

            except Exception as e:
                self.session.rollback()
                retries += 1
                if retries > max_retries:
                    print(f'Exceeded max try limit for creating surrounding character statuses. {traceback.print_exc()}')
                    return {"message": "Failed to create surrounding character statuses. Retry limit exceeded.", "status": "failure"}
                else:
                    print(f'Error occurred while creating surrounding character statuses. Retrying... {traceback.print_exc()}')

        return {"message": "Failed to create surrounding character statuses after multiple attempts.", "status": "failure"}

    def create_surrounding_characters_relationships(self):
        retries = 0
        max_retries = 5
        if not hasattr(self, 'NPCs_data') or not self.NPCs_data:
            return {"message": "No NPC data available to form relationships.", "status": "failure"}

        while retries <= max_retries:
            try:
                num_pairs = min(len(self.NPCs_data), 10)  # Adjust based on your requirements

                # Generate a list of unique pairs
                random_pairs = random.sample([(i, j) for i in range(len(self.NPCs_data)) for j in range(i+1, len(self.NPCs_data))], num_pairs)

                for (i, j) in random_pairs:
                    character = self.NPCs_data[i]
                    related_character = self.NPCs_data[j]

                    relationship_prompt = WORLD_BUILDING['CHARACTER_RELATIONSHIP'].format(character, related_character, self.seed_data)
                    relationship_text = self.get_gpt_response(relationship_prompt)
                    relationship_data = self.extract_json(relationship_text, list_flag=False, nested_key='relationship')

                    if not relationship_data:
                        continue  # If no data extracted, skip to next pair

                    new_relationship = CharacterRelationship(
                        seed_id=self.seed_id,
                        character_id=character['id'],
                        related_character_id=related_character['id'],
                        relationship_type=relationship_data['type'],
                        attraction=relationship_data.get('attraction', 5),
                        respect=relationship_data.get('respect', 5),
                        trust=relationship_data.get('trust', 5),
                        familiarity=relationship_data.get('familiarity', 0),
                        anger=relationship_data.get('anger', 5),
                        fear=relationship_data.get('fear', 5),
                        created_at=datetime.now(),
                        updated_at=datetime.now()
                    )
                    self.session.add(new_relationship)

                self.session.commit()
                return {"message": "Random NPC relationships established successfully", "status": "success"}

            except Exception as e:
                self.session.rollback()
                retries += 1
                if retries > max_retries:
                    print(f'Exceeded max try limit for establishing random NPC relationships. {traceback.print_exc()}')
                    return {"message": "Failed to establish random NPC relationships. Retry limit exceeded.", "status": "failure"}
                else:
                    print(f'Error occurred while establishing random NPC relationships. Retrying... {traceback.print_exc()}')

        return {"message": "Failed to establish random NPC relationships after multiple attempts.", "status": "failure"}

    def create_surrounding_characters_items(self):
        retries = 0
        max_retries = 5
        if not hasattr(self, 'NPCs_data') or not self.NPCs_data:
            return {"message": "No NPC data available to assign items.", "status": "failure"}

        while retries <= max_retries:
            try:
                for npc in self.NPCs_data:
                    # Generate items for each NPC
                    items_text = self.get_gpt_response(WORLD_BUILDING['CHARACTER_ITEMS'].format(npc, self.seed_data))
                    items_data = self.extract_json(items_text, list_flag=True)

                    if items_data is None:
                        print(f"No valid JSON data was extracted for items of character {npc['name']}.")
                        continue

                    for item_data in items_data:
                        new_item = Item(
                            name=item_data['name'],
                            description=item_data['description'],
                            type=item_data['type'],
                            value=item_data.get('value', 0.0),
                            weight=item_data.get('weight', 0.0),
                            created_at=datetime.now(),
                            updated_at=datetime.now()
                        )
                        self.session.add(new_item)
                        self.session.flush()  # Flush to get the item_id before committing

                        new_character_item = CharacterItem(
                            seed_id=self.seed_id,
                            character_id=npc['id'],  # Use the stored NPC ID from self.NPCs_data
                            item_id=new_item.id,
                            quantity=item_data.get('quantity', 1),
                            condition=item_data.get('condition', 100.0),  # Default condition to 100%
                            created_at=datetime.now(),
                            updated_at=datetime.now()
                        )
                        self.session.add(new_character_item)

                    self.session.commit()
                    print(f"Items assigned successfully to character {npc['name']}")

                return {"message": "Items for all surrounding characters created successfully", "status": "success"}

            except Exception as e:
                self.session.rollback()
                retries += 1
                if retries > max_retries:
                    print(f'Exceeded max try limit for assigning items to NPCs. {traceback.print_exc()}')
                    return {"message": "Failed to assign items to NPCs. Retry limit exceeded.", "status": "failure"}
                else:
                    print(f'Error occurred while assigning items to NPCs. Retrying... {traceback.print_exc()}')

        return {"message": "Failed to assign items to NPCs after multiple attempts.", "status": "failure"}
