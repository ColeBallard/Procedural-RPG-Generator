import os
import yaml
import json
import logging

from dotenv import load_dotenv

from flask import Flask, current_app
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from openai import OpenAI

from .orm import Base, Settings
from .world_building.world_building import WorldBuilder

def createApp():
    app = Flask(__name__, static_folder='static', static_url_path='/static', template_folder='templates')

    # Load environment variables
    load_dotenv()

    # Configure session
    app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')

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

    # Create the SQLAlchemy engine with reduced pool size
    engine = create_engine(
        connection_string,
        pool_size=3,
        max_overflow=2
    )
    Base.metadata.create_all(engine)

    # Setup configuration and other necessary initializations here
    # Configure OpenAI SDK to use xAI's Grok API
    with app.app_context():
        current_app.world_builder = None  # Store world_builder in the current_app context
        # Create OpenAI client configured for xAI Grok API
        current_app.openai = OpenAI(
            api_key="placeholder",  # Will be set per request
            base_url="https://api.x.ai/v1"
        )

    # Create a configured "Session" class
    Session = sessionmaker(bind=engine)

    # Store the session factory in the app config
    app.config['SESSION_FACTORY'] = Session

    # Load settings from database (or migrate from YAML if needed)
    session = Session()
    try:
        settings_record = session.query(Settings).first()

        if not settings_record:
            # Migrate from YAML if it exists
            config_path = os.path.join(app.root_path, 'config', 'game_config.yaml')
            if os.path.exists(config_path):
                logging.info("Migrating settings from YAML to database...")
                with open(config_path, 'r') as config_file:
                    config_data = yaml.safe_load(config_file)

                settings_record = Settings(
                    min_grok=config_data.get('min_grok', 'grok-2-1212'),
                    max_grok=config_data.get('max_grok', 'grok-2-1212'),
                    emotional_attributes=json.dumps(config_data.get('emotional_attributes', {})),
                    classes=json.dumps(config_data.get('classes', []))
                )
                session.add(settings_record)
                session.commit()
                logging.info("Settings migrated successfully")
            else:
                # Create default settings
                logging.info("Creating default settings...")
                settings_record = Settings(
                    min_grok='grok-2-1212',
                    max_grok='grok-2-1212',
                    emotional_attributes=json.dumps({}),
                    classes=json.dumps([])
                )
                session.add(settings_record)
                session.commit()

        # Load settings into app config
        app.config['min_grok'] = settings_record.min_grok
        app.config['max_grok'] = settings_record.max_grok
        app.config['emotional_attributes'] = json.loads(settings_record.emotional_attributes) if settings_record.emotional_attributes else {}
        app.config['classes'] = json.loads(settings_record.classes) if settings_record.classes else []

        logging.info("Settings loaded from database")
    except Exception as e:
        logging.error(f"Error loading settings: {e}")
        # Fallback to defaults
        app.config['min_grok'] = 'grok-2-1212'
        app.config['max_grok'] = 'grok-2-1212'
        app.config['emotional_attributes'] = {}
        app.config['classes'] = []
    finally:
        session.close()

    # Register blueprints
    from app.routes import main as main_blueprint
    app.register_blueprint(main_blueprint)

    logging.info("App creation complete")

    return app
