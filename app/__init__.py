import os
import secrets
import yaml
import json
import logging
from datetime import timedelta

from dotenv import load_dotenv

from flask import Flask, abort, current_app, request
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from openai import OpenAI

from .orm import Base, Settings
from .startup import run_startup_tasks
from .world_building.world_building import WorldBuilder

# Double-submit-cookie CSRF: a non-HttpOnly cookie holds a per-browser token
# that the frontend echoes back in the X-CSRF-Token header on state-changing
# requests. Same-origin only, so SameSite=Lax cookies prevent cross-site
# attackers from reading the cookie and forging the header.
CSRF_COOKIE_NAME = 'csrf_token'
CSRF_HEADER_NAME = 'X-CSRF-Token'
_CSRF_SAFE_METHODS = {'GET', 'HEAD', 'OPTIONS'}


def _csrf_protect():
    if not current_app.config.get('CSRF_ENABLED'):
        return
    if request.method in _CSRF_SAFE_METHODS:
        return
    cookie = request.cookies.get(CSRF_COOKIE_NAME)
    header = request.headers.get(CSRF_HEADER_NAME)
    if not cookie or not header or not secrets.compare_digest(cookie, header):
        abort(403, description='CSRF token missing or invalid')


def _csrf_set_cookie(response):
    if not current_app.config.get('CSRF_ENABLED'):
        return response
    if not request.cookies.get(CSRF_COOKIE_NAME):
        response.set_cookie(
            CSRF_COOKIE_NAME, secrets.token_urlsafe(32),
            samesite='Lax',
            secure=current_app.config.get('SESSION_COOKIE_SECURE', False),
            httponly=False,
        )
    return response


def createApp():
    app = Flask(__name__, static_folder='static', static_url_path='/static', template_folder='templates')

    # Load environment variables
    load_dotenv()

    # Session signing key. Refuse to fall back to a hard-coded default so a
    # missing SECRET_KEY in production cannot be exploited to forge session
    # cookies. In non-production we generate an ephemeral random key and
    # warn loudly; sessions will be invalidated on every restart until the
    # operator sets SECRET_KEY in the .env.
    secret_key = os.getenv('SECRET_KEY')
    if not secret_key:
        if (os.getenv('FLASK_ENV') or '').lower() == 'production':
            raise RuntimeError(
                "SECRET_KEY environment variable is required in production"
            )
        secret_key = secrets.token_hex(32)
        logging.warning(
            "SECRET_KEY not set; using an ephemeral random key. "
            "Sessions will be invalidated on every restart. Set SECRET_KEY in your .env."
        )
    app.config['SECRET_KEY'] = secret_key

    # Cookie hardening. Secure flag is opt-in via env so local HTTP dev
    # still works; production deployments behind TLS should set
    # SESSION_COOKIE_SECURE=1.
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    app.config['SESSION_COOKIE_SECURE'] = (
        os.getenv('SESSION_COOKIE_SECURE', '0') == '1'
    )

    # Lifetime applied to sessions that opt in to "Stay signed in" via the
    # login form. Configurable via env so operators can tune it without a
    # code change; defaults to 30 days.
    try:
        remember_days = int(os.getenv('REMEMBER_ME_DAYS', '30'))
    except ValueError:
        remember_days = 30
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=remember_days)

    # Toggle CSRF, login-required and Grok-key-required gates. All default
    # to on for the real app; tests construct their own Flask app without
    # these flags so they bypass the gates.
    app.config['CSRF_ENABLED'] = True
    app.config['LOGIN_REQUIRED'] = True
    app.config['GROK_API_KEY_REQUIRED'] = True
    app.before_request(_csrf_protect)
    app.after_request(_csrf_set_cookie)

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

    # Apply idempotent column adds and (optionally) background-seed
    # NameLibrary if empty. Both steps are gated by env vars and never
    # block app startup.
    run_startup_tasks(engine, Session)

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
                    min_grok=config_data.get('min_grok', 'grok-4-1-fast-non-reasoning'),
                    max_grok=config_data.get('max_grok', 'grok-4.3'),
                    emotional_attributes=json.dumps(config_data.get('emotional_attributes', {})),
                )
                session.add(settings_record)
                session.commit()
                logging.info("Settings migrated successfully")
            else:
                # Create default settings
                logging.info("Creating default settings...")
                settings_record = Settings(
                    min_grok='grok-4-1-fast-non-reasoning',
                    max_grok='grok-4.3',
                    emotional_attributes=json.dumps({}),
                )
                session.add(settings_record)
                session.commit()

        # Load settings into app config
        app.config['min_grok'] = settings_record.min_grok
        app.config['max_grok'] = settings_record.max_grok
        app.config['emotional_attributes'] = json.loads(settings_record.emotional_attributes) if settings_record.emotional_attributes else {}

        logging.info("Settings loaded from database")
    except Exception as e:
        logging.error(f"Error loading settings: {e}")
        # Fallback to defaults
        app.config['min_grok'] = 'grok-4-1-fast-non-reasoning'
        app.config['max_grok'] = 'grok-4.3'
        app.config['emotional_attributes'] = {}
    finally:
        session.close()

    # Register blueprints
    from app.routes import main as main_blueprint
    app.register_blueprint(main_blueprint)

    logging.info("App creation complete")

    return app
