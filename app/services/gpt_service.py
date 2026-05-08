# gpt_service.py
import json


class GPTService:
    def __init__(self, openai, model):
        self.openai = openai
        self.model = model

    def get_response(self, prompt, json_mode=False, temperature=None):
        kwargs = {
            'model': self.model,
            'messages': [{'role': 'user', 'content': prompt}],
        }
        if json_mode:
            kwargs['response_format'] = {'type': 'json_object'}
        if temperature is not None:
            kwargs['temperature'] = temperature
        response = self.openai.chat.completions.create(**kwargs)
        return response.choices[0].message.content.strip()

    def get_structured(self, prompt, schema, max_attempts=2, temperature=None):
        """Call the LLM and validate the response against a Pydantic schema.

        Returns a parsed model instance on success, or ``None`` after
        exhausting ``max_attempts``.

        Args:
            prompt: The prompt string to send to the LLM.
            schema: A Pydantic model class to validate the response against.
            max_attempts: Number of retry attempts before giving up.
            temperature: Optional sampling temperature (0.0–2.0). Higher values
                produce more varied output; lower values are more deterministic.
                Defaults to the model's built-in default when ``None``.

        Failure modes recovered from per-attempt:
          * model refused JSON-mode -> retry without it
          * unparseable JSON         -> retry
          * Pydantic validation error -> retry
        """
        last_error = None
        for attempt in range(1, max_attempts + 1):
            try:
                try:
                    text = self.get_response(prompt, json_mode=True, temperature=temperature)
                except Exception:
                    text = self.get_response(prompt, temperature=temperature)

                data = self._parse_json_payload(text)
                if data is None:
                    last_error = 'no JSON could be extracted from response'
                    continue

                return schema.model_validate(data)
            except Exception as e:
                last_error = e
                print(f'get_structured attempt {attempt}/{max_attempts} failed: {e}')
                continue
        print(f'get_structured exhausted retries; last error: {last_error}')
        return None

    @staticmethod
    def _parse_json_payload(text):
        try:
            start = text.index('{')
            end = text.rindex('}') + 1
            return json.loads(text[start:end])
        except Exception as e:
            print(f'Failed to parse JSON payload: {e}')
            return None


