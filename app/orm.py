import os
import random
from datetime import datetime
from dotenv import load_dotenv
from sqlalchemy import create_engine, Column, Integer, Float, String, DateTime, ForeignKey, SmallInteger, BigInteger, Text, Boolean, Index
from sqlalchemy.dialects import mysql
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker

# Existing tables (created via database/ddl.sql) use ``int unsigned`` for id /
# seed_id columns. New tables created through ``Base.metadata.create_all`` must
# match that signedness or MySQL rejects the foreign key with errno 3780.
UnsignedInt = Integer().with_variant(mysql.INTEGER(unsigned=True), 'mysql')

# Load environment variables
load_dotenv()

# Database configuration
db_config = {
    "host": os.getenv("DB_HOST"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASS"),
    "database": os.getenv("DB_NAME"),
    "port": os.getenv("DB_PORT")
}

# Create the connection string
connection_string = f"mysql+pymysql://{db_config['user']}:{db_config['password']}@{db_config['host']}:{db_config['port']}/{db_config['database']}"

# Create the SQLAlchemy engine
engine = create_engine(connection_string)
Base = declarative_base()

class User(Base):
    __tablename__ = 'Users'
    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(64), unique=True, nullable=False)
    email = Column(String(128), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now)

class Seed(Base):
    __tablename__ = 'Seeds'
    id = Column(Integer, primary_key=True, default=lambda: random.randint(100000, 999999))
    # Owning user. Nullable so the column can be added on existing databases
    # without a backfill; routes treat ``user_id IS NULL`` as inaccessible
    # when LOGIN_REQUIRED is on so orphaned legacy seeds are not exposed
    # to other users.
    user_id = Column(Integer, ForeignKey('Users.id'))
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now)
    current_date_time = Column(DateTime)
    current_turn = Column(Integer, default=1)
    naming_themes = Column(Text)  # JSON: [{"source": "...", "theme": "..."}]
    # Phase 5 (autonomous events): the in-world datetime at which the
    # background-event simulator last ran. The simulator only fires when
    # ``current_date_time`` has moved beyond ``last_event_sim_at`` by
    # at least ``world_simulation.MIN_INTERVAL_MINUTES`` so a string of
    # rapid turns doesn't spam the off-screen world with events.
    last_event_sim_at = Column(DateTime)
    characters = relationship('Character', back_populates='seed')
    character_items = relationship('CharacterItem', back_populates='seed')
    quests = relationship('Quest', back_populates='seed')
    character_quests = relationship('CharacterQuest', back_populates='seed')
    character_relationships = relationship('CharacterRelationship', back_populates='seed')
    character_skill = relationship('CharacterSkill', back_populates='seed')


class Character(Base):
    __tablename__ = 'Characters'
    id = Column(Integer, primary_key=True, autoincrement=True)
    seed_id = Column(Integer, ForeignKey('Seeds.id'), nullable=False)
    main_character = Column(Boolean, default=False)
    alive = Column(Boolean, default=True)
    name = Column(String(64))
    date_of_birth = Column(DateTime)
    race = Column(String(64))
    gender = Column(Boolean)
    level = Column(SmallInteger)
    exp_points = Column(BigInteger)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now)
    strength = Column(SmallInteger)
    speed = Column(SmallInteger)
    agility = Column(SmallInteger)
    intelligence = Column(SmallInteger)
    wisdom = Column(SmallInteger)
    charisma = Column(SmallInteger)
    current_health = Column(Integer)
    max_health = Column(Integer)
    current_currency = Column(Integer)
    # ElevenLabs voice id assigned to this character at world-building time
    # (or NULL when no ElevenLabs key was configured / no match was found).
    # The narrator uses a fixed voice id resolved at TTS time and is never
    # persisted here.
    voice_id = Column(String(64))
    # Where this character physically is right now. NULL means the row
    # predates the travel system; the read path falls back to the seed's
    # first top-level Location (the historical starting location). Used
    # by app/services/travel_service.py to drive the /travel endpoint and
    # by _build_turn_context to anchor the prompt's "Current location".
    current_location_id = Column(Integer, ForeignKey('Locations.id'))
    seed = relationship('Seed', back_populates='characters')
    current_location = relationship('Location', foreign_keys=[current_location_id])


class Item(Base):
    __tablename__ = 'Items'
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(64))
    description = Column(Text)
    type = Column(String(64))
    value = Column(Float)
    weight = Column(Float)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now)


class CharacterItem(Base):
    __tablename__ = 'CharacterItems'
    id = Column(Integer, primary_key=True, autoincrement=True)
    seed_id = Column(Integer, ForeignKey('Seeds.id'), nullable=False)
    character_id = Column(Integer, ForeignKey('Characters.id'), nullable=False)
    item_id = Column(Integer, ForeignKey('Items.id'), nullable=False)
    quantity = Column(Integer)
    condition = Column(Float)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now)
    seed = relationship('Seed', back_populates='character_items')
    character = relationship('Character')
    item = relationship('Item')


class Quest(Base):
    __tablename__ = 'Quests'
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128))
    description = Column(Text)
    start_date_time = Column(DateTime)
    end_date_time = Column(DateTime)
    start_turn = Column(Integer)
    end_turn = Column(Integer)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now)
    seed_id = Column(Integer, ForeignKey('Seeds.id'), nullable=False)
    currency_reward = Column(Integer)
    exp_reward = Column(Integer)
    seed = relationship('Seed', back_populates='quests')
    steps = relationship('QuestStep', back_populates='quest')
    character_quests = relationship('CharacterQuest', back_populates='quest')


class CharacterQuest(Base):
    __tablename__ = 'CharacterQuests'
    id = Column(Integer, primary_key=True, autoincrement=True)
    seed_id = Column(Integer, ForeignKey('Seeds.id'), nullable=False)
    character_id = Column(Integer, ForeignKey('Characters.id'), nullable=False)
    quest_id = Column(Integer, ForeignKey('Quests.id'), nullable=False)
    progress = Column(Float)
    current_step = Column(SmallInteger)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now)
    seed = relationship('Seed', back_populates='character_quests')
    character = relationship('Character')
    quest = relationship('Quest', back_populates='character_quests')


class CharacterRelationship(Base):
    __tablename__ = 'CharacterRelationships'
    id = Column(Integer, primary_key=True, autoincrement=True)
    seed_id = Column(Integer, ForeignKey('Seeds.id'), nullable=False)
    character_id = Column(Integer, ForeignKey('Characters.id'), nullable=False)
    related_character_id = Column(Integer, ForeignKey('Characters.id'), nullable=False)
    relationship_type = Column(String(64))
    attraction = Column(SmallInteger, default=5)
    respect = Column(SmallInteger, default=5)
    trust = Column(SmallInteger, default=5)
    familiarity = Column(SmallInteger, default=0)
    anger = Column(SmallInteger, default=5)
    fear = Column(SmallInteger, default=5)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now)
    seed = relationship('Seed', back_populates='character_relationships')
    character = relationship('Character', foreign_keys=[character_id], backref='relationships')
    related_character = relationship('Character', foreign_keys=[related_character_id])

class Skill(Base):
    __tablename__ = 'Skills'
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(64))
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now)

class CharacterSkill(Base):
    __tablename__ = 'CharacterSkills'
    id = Column(Integer, primary_key=True, autoincrement=True)
    seed_id = Column(Integer, ForeignKey('Seeds.id'), nullable=False)
    character_id = Column(Integer, ForeignKey('Characters.id'), nullable=False)
    skill_id = Column(Integer, ForeignKey('Skills.id'), nullable=False)
    level = Column(SmallInteger)
    exp_points = Column(BigInteger)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now)
    seed = relationship('Seed', back_populates='character_skill')
    character = relationship('Character')
    skill = relationship('Skill')

# Statuses
class Status(Base):
    __tablename__ = 'Statuses'
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(64))
    description = Column(Text)
    type = Column(String(64))
    duration = Column(Float)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now)

class CharacterStatus(Base):
    __tablename__ = 'CharacterStatuses'
    id = Column(Integer, primary_key=True, autoincrement=True)
    seed_id = Column(Integer, ForeignKey('Seeds.id'), nullable=False)
    character_id = Column(Integer, ForeignKey('Characters.id'), nullable=False)
    status_id = Column(Integer, ForeignKey('Statuses.id'), nullable=False)
    active = Column(Boolean)
    end_date_time = Column(DateTime)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now)
    seed = relationship('Seed')
    character = relationship('Character')
    status = relationship('Status')

# Events and Locations
class Event(Base):
    __tablename__ = 'Events'
    id = Column(Integer, primary_key=True, autoincrement=True)
    seed_id = Column(Integer, ForeignKey('Seeds.id'), nullable=False)
    name = Column(String(64))
    description = Column(Text)
    start_date_time = Column(DateTime)
    end_date_time = Column(DateTime)
    type = Column(String(64))
    location_id = Column(Integer, ForeignKey('Locations.id'), nullable=False)
    start_turn = Column(Integer)
    end_turn = Column(Integer)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now)
    seed = relationship('Seed')
    location = relationship('Location', back_populates='events')

class EventCharacter(Base):
    __tablename__ = 'EventCharacters'
    id = Column(Integer, primary_key=True, autoincrement=True)
    seed_id = Column(Integer, ForeignKey('Seeds.id'), nullable=False)
    character_id = Column(Integer, ForeignKey('Characters.id'), nullable=False)
    event_id = Column(Integer, ForeignKey('Events.id'), nullable=False)
    role = Column(String(64))
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now)
    seed = relationship('Seed')
    character = relationship('Character')
    event = relationship('Event')

class Location(Base):
    __tablename__ = 'Locations'
    id = Column(Integer, primary_key=True, autoincrement=True)
    seed_id = Column(Integer, ForeignKey('Seeds.id'), nullable=False)
    name = Column(String(64))
    description = Column(Text)
    longitude = Column(Float)
    latitude = Column(Float)
    type = Column(String(64))
    climate = Column(String(32))
    terrain = Column(String(64))
    parent_id = Column(Integer, ForeignKey('Locations.id'))
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now)
    seed = relationship('Seed')
    parent = relationship('Location', remote_side=[id], back_populates='children')
    children = relationship('Location', back_populates='parent')
    events = relationship('Event', back_populates='location')


class LocationConnection(Base):
    """Undirected edge between two top-level locations (settlements).

    Persisted at world-build time from the LLM's ``connections`` payload so
    the in-game map can render roads/paths/rivers, and so future narrative
    prompts can reason about how the player travels between settlements.
    Sub-locations are not linked here -- those cluster inside a settlement
    and don't need their own edge list.
    """
    __tablename__ = 'LocationConnections'
    # ``Seeds.id`` and ``Locations.id`` are declared ``int unsigned`` in the
    # legacy ddl.sql; MySQL refuses an FK between an unsigned column and a
    # plain signed INTEGER (errno 3780) so we mirror the unsigned variant
    # here for create_all() to produce a compatible schema.
    id = Column(UnsignedInt, primary_key=True, autoincrement=True)
    seed_id = Column(UnsignedInt, ForeignKey('Seeds.id'), nullable=False)
    from_location_id = Column(UnsignedInt, ForeignKey('Locations.id'), nullable=False)
    to_location_id = Column(UnsignedInt, ForeignKey('Locations.id'), nullable=False)
    name = Column(String(128))
    type = Column(String(32), default='road')
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now)
    seed = relationship('Seed')
    from_location = relationship('Location', foreign_keys=[from_location_id])
    to_location = relationship('Location', foreign_keys=[to_location_id])


class GeographicFeature(Base):
    """A region- or line-shaped natural feature (forest, river, mountain
    range, lake, ...) belonging to a seed.

    The polygon / polyline geometry is stored as a JSON string of
    [longitude, latitude] pairs in the same arbitrary 'world units' as
    ``Location.longitude`` / ``Location.latitude``. ``closed`` flags how
    the renderer should treat it: True for filled polygons, False for
    stroked polylines. We use Text + JSON rather than MySQL's spatial
    types because the dataset is tiny (a few features per seed) and the
    JSON shape is exactly what the frontend Leaflet layer consumes.
    """
    __tablename__ = 'GeographicFeatures'
    id = Column(UnsignedInt, primary_key=True, autoincrement=True)
    seed_id = Column(UnsignedInt, ForeignKey('Seeds.id'), nullable=False)
    name = Column(String(128))
    type = Column(String(32), default='forest')
    description = Column(Text)
    geometry = Column(Text)  # JSON: [[lon, lat], [lon, lat], ...]
    closed = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now)
    seed = relationship('Seed')


# QuestSteps
class QuestStep(Base):
    __tablename__ = 'QuestSteps'
    id = Column(Integer, primary_key=True, autoincrement=True)
    quest_id = Column(Integer, ForeignKey('Quests.id'), nullable=False)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now)
    order = Column(SmallInteger)
    quest = relationship('Quest', back_populates='steps')
    seed_id = Column(Integer, ForeignKey('Seeds.id'), nullable=False)
    seed = relationship('Seed')

# NameLibrary: pre-seeded pool of names sourced from external libraries.
# Populated once via scripts/seed_name_library.py and queried at world-building
# time by NameService to assign names that match the seed's chosen themes.
class NameLibrary(Base):
    __tablename__ = 'NameLibrary'
    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String(32), nullable=False, index=True)
    theme = Column(String(64), nullable=False, index=True)
    gender = Column(String(16), nullable=False, default='any', index=True)
    category = Column(String(16), nullable=False, default='first', index=True)
    name = Column(String(128), nullable=False)
    meaning = Column(Text)
    origin = Column(String(64))
    created_at = Column(DateTime, default=datetime.now)

# TranscriptEntry: chronological log of every text shown in the game UI for a
# seed (world-building progress, narration, player input, combat output, etc.).
# Persisted so the narrative panel can be replayed on refresh/resume. The id
# column doubles as the canonical ordering tiebreaker within a seed.
class TranscriptEntry(Base):
    __tablename__ = 'TranscriptEntries'
    id = Column(UnsignedInt, primary_key=True, autoincrement=True)
    seed_id = Column(UnsignedInt, ForeignKey('Seeds.id'), nullable=False)
    turn = Column(Integer)
    kind = Column(String(32), nullable=False)
    speaker = Column(String(64))
    text = Column(Text, nullable=False)
    meta = Column(Text)  # JSON string for kind-specific structured data
    created_at = Column(DateTime, default=datetime.now)
    seed = relationship('Seed')
    __table_args__ = (Index('idx_transcript_seed_id', 'seed_id', 'id'),)

# Scenario: a typed, mid-turn interaction (battle, dialogue, trade, ...) that
# replaces the free-form narrator loop with a structured action vocabulary.
# Only one Scenario per seed is ``active`` at a time; the per-turn route
# checks for it and routes the player's input through the scenario handler
# instead of the standard narration prompt while it lives.
#
# ``state`` is an opaque JSON blob whose shape is owned by the scenario kind
# (see app/scenarios/<kind>.py). Storing per-kind data here rather than in
# kind-specific tables keeps the schema flat for future kinds (crafting,
# stealth, ...) and lets the substrate stay agnostic of any one kind's
# internals.
class Scenario(Base):
    __tablename__ = 'Scenarios'
    id = Column(UnsignedInt, primary_key=True, autoincrement=True)
    seed_id = Column(UnsignedInt, ForeignKey('Seeds.id'), nullable=False)
    kind = Column(String(32), nullable=False)
    status = Column(String(16), nullable=False, default='active')
    state = Column(Text)  # JSON owned by the scenario handler
    summary = Column(Text)  # one-line resolution summary, written on close
    turn_started = Column(Integer)
    turn_ended = Column(Integer)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now)
    resolved_at = Column(DateTime)
    seed = relationship('Seed')
    participants = relationship('ScenarioParticipant', back_populates='scenario',
                                cascade='all, delete-orphan')
    __table_args__ = (Index('idx_scenarios_seed_status', 'seed_id', 'status'),)


class ScenarioParticipant(Base):
    """A character taking part in a scenario.

    ``role`` is the slot the participant fills inside the scenario kind
    (e.g. 'player' / 'npc' for dialogue, 'player' / 'opponent' for battle,
    'player' / 'merchant' for trade). ``order_index`` orders combatants for
    initiative or NPCs for display; defaults to insertion order.
    """
    __tablename__ = 'ScenarioParticipants'
    id = Column(UnsignedInt, primary_key=True, autoincrement=True)
    scenario_id = Column(UnsignedInt, ForeignKey('Scenarios.id'), nullable=False)
    character_id = Column(Integer, ForeignKey('Characters.id'), nullable=False)
    role = Column(String(32), nullable=False, default='participant')
    order_index = Column(SmallInteger, default=0)
    created_at = Column(DateTime, default=datetime.now)
    scenario = relationship('Scenario', back_populates='participants')
    character = relationship('Character')


# Settings
class Settings(Base):
    __tablename__ = 'Settings'
    id = Column(Integer, primary_key=True, autoincrement=True)
    min_grok = Column(String(64), default='grok-4-1-fast-non-reasoning')
    max_grok = Column(String(64), default='grok-4.3')
    emotional_attributes = Column(Text)  # JSON string
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now)

# Create a configured "Session" class
Session = sessionmaker(bind=engine)
