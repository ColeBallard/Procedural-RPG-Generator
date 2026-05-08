CONDENSE = "Condense the following text to make it more concise:\n\n{}"

STEREOTYPE_ANALYSIS = """Oh great, another person who thinks uploading their photo will magically make them interesting. Let me roast—I mean, analyze—this image and create a brutally honest, stereotypical RPG character build based on what I'm seeing.

Look at this person. Really look at them. Now, based on their appearance, style, and whatever desperate cry for attention they're displaying, generate a complete game character build that SCREAMS what kind of basic, predictable character this person would obviously create.

**Your Mission (Should You Choose to Mock It):**

**Character Basics:**
- character_name: Give them a hilariously fitting character name that perfectly captures their try-hard energy or complete lack thereof. Be creative and savage.
- character_age: Pick an age for the character (not their real age, obviously—we're not that cruel... yet)
- character_gender: Character gender (male, female, or other)
- character_class: Choose from: {classes} - Pick the most stereotypically obvious one based on their vibe

**Story & Theme:**
- story_inspiration: A detailed, sarcastic description of the story theme and setting this person would OBVIOUSLY gravitate toward. Mock their predictable genre preferences (fantasy, sci-fi, horror, etc.), their probably-too-serious tone, and the cliché story elements they'd think are "deep and original." Be specific and cutting.

**The Roast:**
- description: A brutally honest, sarcastic 3-4 sentence explanation of why this build is SO OBVIOUSLY perfect for them. Make fun of their appearance, style choices, expression, accessories, or whatever else is screaming for attention in this photo. Be mean but clever. Channel your inner comedy roast energy.

Don't hold back. Be sarcastic, be snarky, be savage. This person asked for it by uploading their photo. Make it hurt (in a funny way).

Please output your response in JSON format with the following structure:
{{
    "character_name": "...",
    "character_age": ...,
    "character_gender": "...",
    "character_class": "...",
    "story_inspiration": "...",
    "description": "..."
}}
"""

WORLD_BUILDING = {
    # Each template returns a single batched JSON payload validated against a
    # Pydantic schema in app/world_building/schemas.py.
    'MAIN_CHARACTER_BATCH': (
        "Create a main character based on the following seed data:\n{}\n\n"
        "Return a single JSON object with these fields:\n"
        "  name (string)\n"
        "  date_of_birth (YYYY-MM-DD)\n"
        "  race (string)\n"
        "  gender ('male' or 'female')\n"
        "  current_date_time (YYYY-MM-DD; the in-world date)\n"
        "  skills: array of {{name, description}} (3-6 entries)\n"
        "  statuses: array of {{name, description, type, duration}} (2-4 entries; "
        "include both buffs and debuffs; duration in seconds)\n"
        "Output JSON only."
    ),
    'LOCATIONS_BATCH': (
        "Generate 3-5 top-level locations in close proximity for this world seed:\n{}\n\n"
        "For EACH location also generate 2-4 sub-locations (areas/buildings inside it).\n"
        "Return a single JSON object: {{\"locations\": [ ... ]}} where each location has:\n"
        "  name, description, longitude, latitude, type, climate, terrain,\n"
        "  sub_locations: array of {{name, description, longitude, latitude, type, climate, terrain}}\n"
        "Output JSON only."
    ),
    'NPCS_FOR_LOCATION_BATCH': (
        "Generate 2-4 NPCs that live or work in this location:\n{}\n\n"
        "Seed data for context:\n{}\n\n"
        "Return a single JSON object: {{\"npcs\": [ ... ]}} where each NPC has:\n"
        "  name, date_of_birth (YYYY-MM-DD), race, gender ('male'/'female'),\n"
        "  event: {{name, description, type, role}} (a recent event involving them at this location),\n"
        "  skills: array of {{name, description}} (2-4 entries),\n"
        "  statuses: array of {{name, description, type, duration}} (1-3 entries; duration in seconds),\n"
        "  items: array of {{name, description, type, value, weight, quantity}} (2-4 entries; weight in kg)\n"
        "Output JSON only."
    ),
    'RELATIONSHIP_BATCH': (
        "Create a relationship between this character:\n{}\nAnd this character:\n{}\n"
        "Based on the following seed data:\n{}\n\n"
        "Return a single JSON object with these fields:\n"
        "  type (string), attraction, respect, trust, familiarity, anger, fear "
        "(each integer 1-10).\n"
        "Output JSON only."
    ),
    'INTRO_NARRATIVE': (
        "You are the narrator of a text-based RPG. Compose a short opening "
        "passage (3-5 paragraphs, second-person 'you') that introduces the "
        "player's character and the world they wake into. Set the scene at "
        "the starting location, hint at the wider world and the situation "
        "the character finds themselves in, and end on a beat that invites "
        "the player to act.\n\n"
        "World seed (genre, tone, premise):\n{seed_data}\n\n"
        "Main character:\n{character}\n\n"
        "Starting location:\n{starting_location}\n\n"
        "Other nearby locations:\n{other_locations}\n\n"
        "Write only the narration prose. No headings, no meta commentary, "
        "no bullet lists, no quoted dialogue from the character."
    ),
    'NAMING_THEME_SELECTION': (
        "You are picking the naming aesthetic for an entire RPG world. The choice "
        "you make here will determine what every character in this world is named "
        "for the rest of the game, so it must feel coherent with the world's tone.\n\n"
        "World seed data:\n{}\n\n"
        "Available naming themes (each is a (source, theme) pair backed by a "
        "pre-built name pool):\n{}\n\n"
        "Pick 1-3 (source, theme) pairs that best fit the world. Combine themes "
        "only when the world plausibly mixes cultures (e.g. a cyberpunk city with "
        "Japanese + Germanic naming). Prefer a single theme when in doubt.\n\n"
        "Return a single JSON object: {{\"themes\": [{{\"source\": \"...\", "
        "\"theme\": \"...\"}}, ...], \"reasoning\": \"...\"}}\n"
        "Output JSON only."
    ),
}