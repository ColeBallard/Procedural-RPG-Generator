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
    'MAIN_CHARACTER': "Create a main character based on the following seed data:\n{}\nThe character should have a name, date_of_birth, race, gender, and current_date_time. The current_date_time should be the datetime the character is currently living in. Please use standard date format. Please output to JSON format.",
    'MAIN_CHARACTER_SKILLS': "Generate a list of skills for this main character:\n{}\nBased on the following seed data:\n{}\nEach skill should have a name and description. Please output to JSON format.",
    'MAIN_CHARACTER_STATUSES': "Generate a list of statuses for this main character:\n{}\nBased on the following seed data:\n{}\nEach status should have a name, description, type, and duration (in seconds). Please include buffs and debuffs. Please output to JSON format.",
    'LOCATIONS': "Generate a list of locations that are all in close proximity based on the following seed data:\n{}\nEach location should include a name, description, longitude, latitude, type, climate, and terrain. Please output to JSON format.",
    'SUB_LOCATIONS': "Generate a list of areas or buildings that are all in close proximity inside this location:\n{}\nBased on the following seed data:\n{}\nEach location should include a name, description, longitude, latitude, type, climate, and terrain. Please output to JSON format.",
    'SURROUNDING_CHARACTERS': "Generate a list of NPC's that are all in close proximity inside this location:\n{}\nBased on the following seed data:\n{}\nEach character should have a name, date_of_birth, race, and gender. Please use standard date format. Please output to JSON format.",
    'CHARACTER_EVENT': "Create an event for this NPC:\n{}\nWho is currently in this location:\n{}\nBased on the following seed data:\n{}\nThe event should have a name, description, type, and role. The role should be the characters role in the event. Please output to JSON format.",
    'CHARACTER_SKILLS': "Generate a short list of skills for this NPC:\n{}\nBased on the following seed data:\n{}\nEach skill should have a name and description. Please output to JSON format.",
    'CHARACTER_STATUSES': "Generate a short list of statuses for this NPC:\n{}\nBased on the following seed data:\n{}\nEach status should have a name, description, type, and duration (in seconds). Please include buffs and debuffs. Please output to JSON format.",
    'CHARACTER_RELATIONSHIP': 'Create a relationship between this character:\n{}\nAnd this character:\n{}\nBased on the following seed data:\n{}\nEach relationship should have a type and level (1-10) of attraction, respect, trust, familiarity, anger, and fear. Please output to JSON format.',
    'CHARACTER_ITEMS': "Generate a short list of items for this NPC:\n{}\nBased on the following seed data:\n{}\nEach items should have a name, description, type, value, weight in kg, and quantity. Please output to JSON format.",
}