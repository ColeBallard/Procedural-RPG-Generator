# gpt_service.py
class GPTService:
    def __init__(self, openai, model):
        self.openai = openai
        self.model = model

    def get_response(self, prompt):
        response = self.openai.chat.completions.create(
            model=self.model,
            messages=[{'role': 'user', 'content': prompt}]
        )
        return response.choices[0].message.content.strip()

    def extract_json(self, text, list_flag=False, nested_key=None):
        import json, traceback
        try:
            if list_flag:
                start_index = text.index("[")
                end_index = text.rindex("]") + 1
            else:
                start_index = text.index("{")
                end_index = text.rindex("}") + 1

            json_str = text[start_index:end_index]
            json_obj = json.loads(json_str)

            if nested_key and nested_key in json_obj:
                json_obj = json_obj[nested_key]

            if list_flag:
                json_obj = [self.remap_object(item) for item in json_obj]
            else:
                json_obj = self.remap_object(json_obj)

            return json_obj
        except Exception as e:
            print("Failed to extract or parse JSON:", e)
            return None

    def remap_fields(self, obj, field_map):
        new_obj = obj.copy()
        for new_field, old_fields in field_map.items():
            for old_field in old_fields:
                if old_field in obj:
                    new_obj[new_field] = obj[old_field]
                    break
        return new_obj

    def remap_object(self, obj):
        from datetime import datetime
        field_map = {
            'name': ['character_name', 'name', 'event_name'],
            'description': ['description', 'event_description'],
            'type': ['type', 'event_type', 'relationship_type'],
            'role': ['role', 'event_role', 'character_role'],
            'date_of_birth': ['date_of_birth', 'birth_date'],
            'race': ['race', 'character_race'],
            'gender': ['gender', 'character_gender'],
            'current_date_time': ['current_date_time', 'current_datetime'],
            'attraction': ['attraction', 'relationship_attraction', 'character_attraction'],
            'respect': ['respect', 'relationship_respect', 'character_respect'],
            'trust': ['trust', 'relationship_trust', 'character_trust'],
            'familiarity': ['familiarity', 'relationship_familiarity', 'character_familiarity'],
            'anger': ['anger', 'relationship_anger', 'character_anger'],
            'fear': ['fear', 'relationship_fear', 'character_fear']
        }

        obj = self.remap_fields(obj, field_map)

        if 'gender' in obj:
            gender_map = {'Female': False, 'Male': True}
            obj['gender'] = gender_map.get(obj['gender'], None)

        if 'date_of_birth' in obj:
            try:
                obj['date_of_birth'] = datetime.strptime(obj['date_of_birth'], "%Y-%m-%d")
            except Exception as e:
                print(f"Can't format date {obj['date_of_birth']} due to {e}.")
                obj['date_of_birth'] = None

        if 'current_date_time' in obj:
            try:
                obj['current_date_time'] = datetime.strptime(obj['current_date_time'], "%Y-%m-%d")
            except Exception as e:
                print(f"Can't format date {obj['current_date_time']} due to {e}.")
                obj['current_date_time'] = None

        return obj
