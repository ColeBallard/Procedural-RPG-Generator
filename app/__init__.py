import os
import yaml
import logging

from dotenv import load_dotenv

from flask import Flask, current_app
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import openai

from .orm import Base
from .world_building.world_building import WorldBuilder

def createApp():
    app = Flask(__name__, static_folder='static', static_url_path='/static', template_folder='templates')

    # Load environment variables
    load_dotenv()

    # Configure session
    app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')

    # Load YAML configuration
    config_path = os.path.join(app.root_path, 'config', 'game_config.yaml')
    with open(config_path, 'r') as config_file:
        config_data = yaml.safe_load(config_file)

    # Store the configuration in the app config
    app.config.update(config_data)

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
    Base.metadata.create_all(engine)

    # Setup configuration and other necessary initializations here
    with app.app_context():
        current_app.world_builder = None  # Store world_builder in the current_app context
        current_app.openai = openai

    # Create a configured "Session" class
    Session = sessionmaker(bind=engine)

    # Store the session factory in the app config
    app.config['SESSION_FACTORY'] = Session

    # Register blueprints
    from app.routes import main as main_blueprint
    app.register_blueprint(main_blueprint)

    logging.info("App creation complete")
    
    return app
