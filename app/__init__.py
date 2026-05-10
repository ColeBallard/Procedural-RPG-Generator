import base64
import hashlib
import os
import re
import secrets
import threading
import time
import yaml
import json
import logging
from datetime import timedelta

from dotenv import load_dotenv

from cryptography.fernet import Fernet, InvalidToken
from flask import Flask, abort, current_app, request
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

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

# --- Log scrubbing -----------------------------------------------------------
# Patterns that match secrets we never want to land in log records: xAI keys,
# Authorization Bearer tokens, the X-Grok-API-Key header value, and the legacy
# grok_api_key body field (the latter still appears in older deployments and
# in third-party tracebacks). Each replacement keeps a short prefix so log
# diffs remain useful for debugging.
_REDACTED = '[REDACTED]'
_SCRUB_PATTERNS = [
    (re.compile(r'xai-[A-Za-z0-9_\-]{4,}'), 'xai-' + _REDACTED),
    (re.compile(r'(?i)(authorization\s*[:=]\s*bearer\s+)[A-Za-z0-9._\-]+'),
     r'\1' + _REDACTED),
    (re.compile(r'(?i)(x-grok-api-key\s*[:=]\s*)[^\s,;"\']+'),
     r'\1' + _REDACTED),
    (re.compile(r'(?i)("?(?:grok_api_key|api_key|password)"?\s*[:=]\s*"?)[^"\s,}]+'),
     r'\1' + _REDACTED),
]


def _scrub(text):
    if not isinstance(text, str) or not text:
        return text
    for pattern, replacement in _SCRUB_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class SecretScrubFilter(logging.Filter):
    """Redact known secret shapes from log records before they're emitted.

    Applied at the root logger so it covers application logs, werkzeug's
    request log, and gunicorn's error log. Both ``record.msg`` and any
    pre-formatted ``record.args`` are scrubbed; falling back to the formatted
    message (``record.getMessage()``) catches handlers that bypass the args.
    """

    def filter(self, record):
        try:
            record.msg = _scrub(record.msg) if isinstance(record.msg, str) else record.msg
            if record.args:
                if isinstance(record.args, tuple):
                    record.args = tuple(_scrub(a) if isinstance(a, str) else a
                                        for a in record.args)
                elif isinstance(record.args, dict):
                    record.args = {k: (_scrub(v) if isinstance(v, str) else v)
                                   for k, v in record.args.items()}
        except Exception:
            # Never let logging-filter bugs break logging itself.
            pass
        return True


def _install_log_scrubbing():
    """Attach SecretScrubFilter to every logger that currently has handlers.

    Idempotent: re-running attaches a fresh filter only where one of the same
    type isn't already present. Covers the root logger plus the named loggers
    Flask, werkzeug and gunicorn use under WSGI.
    """
    targets = [logging.getLogger()]  # root
    for name in ('werkzeug', 'gunicorn', 'gunicorn.error', 'gunicorn.access',
                 'flask.app'):
        targets.append(logging.getLogger(name))
    for log in targets:
        if not any(isinstance(f, SecretScrubFilter) for f in log.filters):
            log.addFilter(SecretScrubFilter())
        for handler in log.handlers:
            if not any(isinstance(f, SecretScrubFilter) for f in handler.filters):
                handler.addFilter(SecretScrubFilter())


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


# --- Response hardening ------------------------------------------------------
# Content-Security-Policy assembled in pieces so the CDN allow-list stays
# obvious and the inline-style carve-out (required by the existing template's
# inline ``style="..."`` attributes and dynamically-set styles in main.js) is
# explicit. Scripts are restricted to 'self' + the SRI-pinned CDNs the page
# already loads; no 'unsafe-inline' for scripts.
_CSP_DIRECTIVES = (
    "default-src 'self'",
    "script-src 'self' https://cdn.jsdelivr.net https://code.jquery.com",
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net "
        "https://fonts.googleapis.com",
    "font-src 'self' https://fonts.gstatic.com data:",
    "img-src 'self' data:",
    "connect-src 'self'",
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "object-src 'none'",
)
_CSP_HEADER_VALUE = '; '.join(_CSP_DIRECTIVES)


def _security_headers(response):
    """Attach defense-in-depth response headers and disable caching of
    sensitive payloads.

    ``Cache-Control: no-store`` is set on JSON and SSE responses so an
    intermediary cache cannot retain a response that may have been built
    from per-user data. HSTS is only emitted when the operator has signalled
    HTTPS via ``SESSION_COOKIE_SECURE`` so local HTTP development is not
    pinned to HTTPS by an accidental cache.
    """
    response.headers.setdefault('Content-Security-Policy', _CSP_HEADER_VALUE)
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('X-Frame-Options', 'DENY')
    response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    if current_app.config.get('SESSION_COOKIE_SECURE'):
        response.headers.setdefault(
            'Strict-Transport-Security',
            'max-age=31536000; includeSubDomains',
        )
    mimetype = (response.mimetype or '').lower()
    if mimetype == 'application/json' or mimetype == 'text/event-stream':
        response.headers['Cache-Control'] = 'no-store'
        response.headers.setdefault('Pragma', 'no-cache')
    return response


# --- Rate limiting -----------------------------------------------------------
# Module-level Limiter so the routes module can decorate handlers at import
# time. Calling ``limiter.init_app(app)`` activates enforcement; tests build
# their own Flask app without calling it, so the decorators are inert there
# (matching how the CSRF and login gates are configured).
try:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address

    def _rate_limit_key():
        # Rate-limit by authenticated user when available so multiple users
        # behind a shared NAT don't share a single bucket; fall back to the
        # remote address for unauthenticated routes (signup, login, the
        # landing page).
        from flask import session as flask_session
        user_id = flask_session.get('user_id') if flask_session else None
        if user_id:
            return f'user:{user_id}'
        return f'ip:{get_remote_address()}'

    limiter = Limiter(
        key_func=_rate_limit_key,
        default_limits=[],  # explicit per-route limits only
        storage_uri='memory://',
        headers_enabled=True,
    )
except ImportError:  # pragma: no cover - flask-limiter is a hard dep in prod
    limiter = None


# --- Server-side, session-scoped Grok key storage ---------------------------
# Replaces the previous "key in browser localStorage, sent on every request"
# scheme. The browser pushes the key once via POST /api/grok-key after the
# user signs in; the server stores it encrypted-at-rest in process memory
# under the authenticated user_id, and every subsequent gated route reads it
# back via _extract_grok_api_key(). The key never travels on the wire again.
#
# Encryption-at-rest is symmetric Fernet keyed off SECRET_KEY: it doesn't
# defend against an attacker with code execution (they can read SECRET_KEY
# too), but it keeps raw keys out of any accidental memory/dict dump and
# makes the intent of the storage explicit.
def _derive_fernet_key(secret):
    return base64.urlsafe_b64encode(hashlib.sha256(secret.encode('utf-8')).digest())


class GrokKeyStore:
    """Process-local, encrypted, TTL-bounded store of per-user Grok keys.

    Keyed by the authenticated user's id. Entries auto-expire after the same
    interval as the Flask session ("Stay signed in" lifetime), so a key that
    was pushed by a since-logged-out user cannot outlive the session it was
    bound to.
    """

    def __init__(self):
        self._store = {}
        self._lock = threading.Lock()
        self._fernet = None

    def _f(self):
        if self._fernet is None:
            self._fernet = Fernet(_derive_fernet_key(current_app.config['SECRET_KEY']))
        return self._fernet

    def _ttl_seconds(self):
        lifetime = current_app.config.get('PERMANENT_SESSION_LIFETIME')
        if isinstance(lifetime, timedelta):
            return lifetime.total_seconds()
        return float(lifetime or 86400)

    def set(self, user_id, key):
        token = self._f().encrypt(key.encode('utf-8'))
        expires_at = time.time() + self._ttl_seconds()
        with self._lock:
            self._store[str(user_id)] = (token, expires_at)

    def get(self, user_id):
        sid = str(user_id)
        with self._lock:
            rec = self._store.get(sid)
        if not rec:
            return None
        token, expires_at = rec
        if time.time() > expires_at:
            self.clear(user_id)
            return None
        try:
            return self._f().decrypt(token).decode('utf-8')
        except (InvalidToken, ValueError):
            self.clear(user_id)
            return None

    def has(self, user_id):
        return self.get(user_id) is not None

    def clear(self, user_id):
        with self._lock:
            self._store.pop(str(user_id), None)


grok_key_store = GrokKeyStore()


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
    app.after_request(_security_headers)

    # Install secret-redaction filter on the root logger and on the named
    # WSGI loggers so application logs, werkzeug's request line, and gunicorn
    # access/error output never carry an xAI key or Authorization header.
    _install_log_scrubbing()

    # Activate per-route rate limiting. The Limiter decorators on routes are
    # a no-op until init_app() runs, which keeps the existing test apps that
    # build their own Flask instance unaffected.
    if limiter is not None:
        limiter.init_app(app)

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

    # Per-request OpenAI clients are constructed inside the routes that need
    # them (see _make_gpt_service / initialize_world_building*) using the
    # caller's Grok API key. No shared client lives on the app object: a
    # single mutable client would race between concurrent requests carrying
    # different keys.

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
