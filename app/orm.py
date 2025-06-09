import os
import random
from datetime import datetime
from dotenv import load_dotenv
from sqlalchemy import create_engine, Column, Integer, Float, String, DateTime, ForeignKey, SmallInteger, BigInteger, Text, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker
from sqlalchemy.pool import Pool
from mysql.connector import pooling

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

# Initialize connection pool
pool = pooling.MySQLConnectionPool(
    pool_name="pgng_pool",
    pool_size=5,
    **db_config
)

# Create the connection string
connection_string = f"mysql+pymysql://{db_config['user']}:{db_config['password']}@{db_config['host']}:{db_config['port']}/{db_config['database']}"

# Create the SQLAlchemy engine
engine = create_engine(connection_string)
Base = declarative_base()

class Seed(Base):
    __tablename__ = 'Seeds'
    id = Column(Integer, primary_key=True, default=lambda: random.randint(100000, 999999))
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now)
    current_date_time = Column(DateTime)
    current_turn = Column(Integer, default=1)
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
    seed = relationship('Seed', back_populates='characters')


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


# Create all tables in the database
Base.metadata.create_all(engine)

# Create a configured "Session" class
Session = sessionmaker(bind=engine)

# Create a session
session = Session()
