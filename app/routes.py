import datetime
import os
import random
import re
import requests
import json
import base64
import queue
import threading
import uuid
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash

from flask import Blueprint, jsonify, render_template, current_app, request, send_from_directory, session, Response, stream_with_context, abort
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from openai import OpenAI

from app.orm import (
    Seed, User, Settings, Character, Location, LocationConnection,
    GeographicFeature, Event, EventCharacter, Quest, CharacterItem, Item,
    CharacterSkill, Skill, CharacterStatus, Status, CharacterRelationship,
    TranscriptEntry, Scenario,
)
from app.services import transcript_service
from app.services import elevenlabs_service
from app.services import time_service
from app.services import dice_service
from app.services import travel_service
from app.services import world_simulation
from app.services.gpt_service import GPTService
from app.services.name_service import NameService
from app.world_building.world_building import WorldBuilder
from app.world_building.schemas import TurnResponseOut, ActionAdjudicationOut
from app.prompt_templates import STEREOTYPE_ANALYSIS, WORLD_BUILDING, ARBITER_ADJUDICATE
from app import scenarios as _scenarios

# Rate limiter is initialised in createApp(); when it isn't available (the
# package failed to import) or hasn't been bound to a test app, ``limit``
# becomes a no-op so the route decorators stay valid in every context.
from app import limiter as _limiter
from app import grok_key_store as _grok_key_store
from app import elevenlabs_key_store as _elevenlabs_key_store


def limit(spec):
    if _limiter is None:
        def _noop(f):
            return f
        return _noop
    return _limiter.limit(spec)

# Cap on how many trailing transcript entries are folded into the per-turn
# context. Keeps prompts bounded as the game grows; the world-building
# progress lines are filtered out to spend the budget on actual story beats.
TURN_TRANSCRIPT_HISTORY = 30

main = Blueprint('main', __name__)
world_builder = None

# Store progress queues for each session
progress_queues = {}


def login_required(f):
    """Reject unauthenticated callers when LOGIN_REQUIRED is enabled.

    Tests construct ad-hoc Flask apps that don't set the flag, so the gate
    is a no-op there. ``createApp`` flips it on for the real app.
    """
    @wraps(f)
    def wrapped(*args, **kwargs):
        if current_app.config.get('LOGIN_REQUIRED') and 'user_id' not in session:
            return jsonify({'success': False, 'message': 'Authentication required'}), 401
        return f(*args, **kwargs)
    return wrapped


def _is_valid_grok_key(key):
    return bool(key) and key.startswith('xai-') and len(key) > 4


def _extract_grok_api_key():
    # Production transport: server-side, session-scoped store. The user
    # pushes the key once via POST /api/grok-key after signing in; the
    # encrypted blob lives in process memory keyed by user_id and is
    # retrieved here on every gated request. The key never travels back to
    # the browser and is never read from a request header or body.
    user_id = session.get('user_id')
    if user_id is not None:
        stored = _grok_key_store.get(user_id)
        if stored:
            return stored
    # Dev convenience: when not in production, createApp() loads GROK_API_KEY
    # from .env into DEV_GROK_API_KEY so the developer doesn't have to push
    # the key via the UI on every restart. Forced to None in production.
    dev_key = current_app.config.get('DEV_GROK_API_KEY')
    if dev_key:
        return dev_key
    # Test/legacy fallback: only honoured when GROK_API_KEY_REQUIRED is off
    # (i.e. the unit-test apps that build their own Flask instance without
    # the gate). The production app always sets the gate, so this branch is
    # unreachable there and an attacker cannot bypass the session store by
    # sending the header directly.
    if not current_app.config.get('GROK_API_KEY_REQUIRED'):
        key = request.headers.get('X-Grok-API-Key')
        if key:
            return key.strip()
    return ''


def _extract_elevenlabs_api_key():
    """Look up the caller's ElevenLabs key from the in-memory store.

    Mirrors ``_extract_grok_api_key`` but is lookup-only: there is no
    ``ELEVENLABS_API_KEY_REQUIRED`` gate because TTS is an opt-in UX layer
    that simply degrades gracefully (silent / cached-only) when no key is
    bound. Falls back to ``DEV_ELEVENLABS_API_KEY`` for the dev workflow.
    """
    user_id = session.get('user_id')
    if user_id is not None:
        stored = _elevenlabs_key_store.get(user_id)
        if stored:
            return stored
    return current_app.config.get('DEV_ELEVENLABS_API_KEY')


def grok_api_key_required(f):
    """Reject callers without a format-valid xAI key when the gate is on.

    Mirrors ``login_required``: tests omit the flag so they bypass it,
    while ``createApp`` flips ``GROK_API_KEY_REQUIRED`` on in the real app.
    """
    @wraps(f)
    def wrapped(*args, **kwargs):
        if current_app.config.get('GROK_API_KEY_REQUIRED'):
            key = _extract_grok_api_key()
            if not _is_valid_grok_key(key):
                return jsonify({
                    'success': False,
                    'message': 'A valid xAI API key is required.'
                }), 403
        return f(*args, **kwargs)
    return wrapped


def _seed_owned_by_caller(db_session, seed_id):
    """Return the Seed if it exists and belongs to the caller, else ``None``.

    Caller pattern::

        seed = _seed_owned_by_caller(db_session, seed_id)
        if seed is None:
            return jsonify({'error': 'Seed not found'}), 404

    A 404 (rather than 403) is the right response for a missing-or-foreign
    seed: returning 403 would leak the existence of seed ids belonging to
    other users and let an attacker enumerate the seed-id space.

    When ``LOGIN_REQUIRED`` is off (test apps that mount the blueprint
    without the auth gate) ownership is *not* enforced and the helper just
    returns the seed if the row exists, mirroring the lookup-by-id
    behaviour the existing tests rely on. In production the gate is on and
    orphan seeds (``user_id IS NULL``, e.g. legacy rows from before the
    migration) are reported as not-found so they cannot be read by every
    signed-in user.
    """
    seed = db_session.query(Seed).filter(Seed.id == seed_id).first()
    if seed is None:
        return None
    if not current_app.config.get('LOGIN_REQUIRED'):
        return seed
    user_id = session.get('user_id')
    if user_id is None or seed.user_id is None or seed.user_id != user_id:
        return None
    return seed


def _seed_id_owned_by_caller(db_session, seed_id):
    """Same ownership check as ``_seed_owned_by_caller`` but only returns
    a boolean. Used by routes that don't otherwise need the Seed row."""
    return _seed_owned_by_caller(db_session, seed_id) is not None

@main.route('/', methods=['GET', 'POST'])
def index():
    return render_template('index.html')

# Frontend Handlebars templates fetched at runtime by main.js. Anything
# not on this allow-list (e.g. index.html itself, or any future server
# scaffolding template) is rejected so the route can't be turned into a
# generic file-disclosure primitive against the templates directory.
_TEMPLATE_ALLOWLIST = frozenset({
    'narrativeItem.hbs',
    'location.html',
    'event.html',
    'interactingCharacter.html',
    'quest.html',
    'item.html',
})


@main.route('/templates/<path:filename>')
@login_required
def serve_template(filename):
    if filename not in _TEMPLATE_ALLOWLIST:
        abort(404)
    return send_from_directory('templates', filename)

@main.route('/create_seed', methods=['POST'])
@login_required
@limit("30 per hour")
def create_seed():
    Session = current_app.config['SESSION_FACTORY']
    db_session = Session()

    # Bind every new seed to the caller so the per-seed routes can later
    # reject foreign access. Tests build their own Flask app without
    # LOGIN_REQUIRED and have no session user_id; the column stays NULL
    # for them, matching the legacy behaviour the existing fixtures rely on.
    owner_id = session.get('user_id')

    max_retries = 5
    attempts = 0

    while attempts < max_retries:
        try:
            new_seed = Seed(user_id=owner_id)
            db_session.add(new_seed)
            db_session.commit()
            return jsonify({"message": "Seed created successfully", "status": "success", "seed_id": new_seed.id}), 201
        except IntegrityError:
            db_session.rollback()  # Rollback the session to a clean state
            attempts += 1
            if attempts == max_retries:
                return jsonify({"message": "Failed to create a seed after multiple attempts", "status": "failure"}), 500
        finally:
            db_session.close()

@main.route('/initialize_world_building', methods=['POST'])
@login_required
@grok_api_key_required
@limit("10 per hour")
def initialize_world_building():
    data = request.get_json(silent=True) or {}
    seed_id = data.get('seed_id')
    seed_data = data.get('seed_data')
    grok_api_key = _extract_grok_api_key()

    Session = current_app.config['SESSION_FACTORY']
    db_session = Session()

    # Reject world-building against a seed the caller does not own so an
    # attacker cannot overwrite another user's freshly-created blank seed.
    if _seed_owned_by_caller(db_session, seed_id) is None:
        db_session.close()
        return jsonify({'error': 'Seed not found'}), 404

    # Build a per-request OpenAI client so concurrent requests with different
    # API keys cannot stomp on each other via shared mutable state. Mirrors
    # the streaming route's pattern.
    openai_client = OpenAI(api_key=grok_api_key, base_url="https://api.x.ai/v1")

    try:
        world_builder = WorldBuilder(
            seed_data, seed_id, db_session, openai_client,
            current_app.config['min_grok'],
            elevenlabs_api_key=_extract_elevenlabs_api_key(),
        )

        # Orchestrate the world-building process by calling the build_world method
        results = world_builder.build_world()

        # Optionally, you can check for errors or partial failures in 'results'
        return jsonify(results), 200
    except Exception:
        db_session.rollback()
        current_app.logger.exception('initialize_world_building failed for seed_id=%s', seed_id)
        return jsonify({"message": "An error occurred during world building"}), 500
    finally:
        db_session.close()

@main.route('/initialize_world_building_stream', methods=['POST'])
@login_required
@grok_api_key_required
@limit("10 per hour")
def initialize_world_building_stream():
    data = request.get_json(silent=True) or {}
    seed_id = data.get('seed_id')
    seed_data = data.get('seed_data')
    grok_api_key = _extract_grok_api_key()
    elevenlabs_api_key = _extract_elevenlabs_api_key()

    # Capture the real app object and config values up front so the background
    # thread does not depend on the request-bound current_app proxy.
    app = current_app._get_current_object()
    session_factory = app.config['SESSION_FACTORY']
    model = app.config['min_grok']

    # Ownership pre-check: reject world-building against a seed the caller
    # does not own before spinning up the SSE worker thread.
    _ownership_check_session = session_factory()
    try:
        if _seed_owned_by_caller(_ownership_check_session, seed_id) is None:
            return jsonify({'error': 'Seed not found'}), 404
    finally:
        _ownership_check_session.close()

    # Build a per-request OpenAI client so concurrent requests with different
    # API keys cannot stomp on each other via shared mutable state.
    openai_client = OpenAI(api_key=grok_api_key, base_url="https://api.x.ai/v1")

    # Use a uuid to guarantee a unique queue id per request.
    session_id = str(uuid.uuid4())
    progress_queues[session_id] = queue.Queue()
    q = progress_queues[session_id]

    def run_world_builder():
        with app.app_context():
            db_session = session_factory()
            try:
                def progress_callback(msg, status='info'):
                    # WorldBuilder emits status='info' for in-progress steps and
                    # status='success' for the final "World building complete!"
                    # message. Both map to the frontend's 'progress' event type;
                    # the terminal 'complete' event is emitted below with the
                    # full results payload. Each message is also persisted as a
                    # TranscriptEntry so the narrative panel can be replayed
                    # after a refresh or resume.
                    transcript_service.add_entry(
                        session_factory, seed_id,
                        transcript_service.KIND_WORLD_BUILDING, msg,
                        status=status,
                    )
                    q.put({'type': 'progress', 'message': msg})

                world_builder = WorldBuilder(
                    seed_data,
                    seed_id,
                    db_session,
                    openai_client,
                    model,
                    progress_callback=progress_callback,
                    elevenlabs_api_key=elevenlabs_api_key,
                )

                results = world_builder.build_world()

                # Persist the opening narration (if produced) as a sequence
                # of paragraph-sized transcript entries so it renders with
                # narration styling, gets replayed on refresh, and lets the
                # TTS layer synthesize one short paragraph at a time instead
                # of blocking on the whole opening passage at once.
                intro_narration = results.pop('intro_narration', None) if isinstance(results, dict) else None
                if intro_narration:
                    for paragraph in _split_paragraphs(intro_narration):
                        transcript_service.add_entry(
                            session_factory, seed_id,
                            transcript_service.KIND_NARRATION, paragraph,
                            speaker='Narrator',
                        )

                q.put({'type': 'complete', 'results': results})
            except Exception:
                db_session.rollback()
                current_app.logger.exception(
                    'initialize_world_building_stream failed for seed_id=%s', seed_id)
                generic = 'World building failed; please retry.'
                transcript_service.add_entry(
                    session_factory, seed_id,
                    transcript_service.KIND_WORLD_BUILDING, generic,
                    status='error',
                )
                q.put({'type': 'error', 'message': generic})
            finally:
                db_session.close()
                q.put(None)

    def generate():
        try:
            threading.Thread(target=run_world_builder, daemon=True).start()
            while True:
                item = q.get()
                if item is None:
                    break
                yield f"data: {json.dumps(item)}\n\n"
        finally:
            progress_queues.pop(session_id, None)

    return Response(stream_with_context(generate()), mimetype='text/event-stream')

@main.route('/api/settings', methods=['GET'])
@login_required
def get_settings():
    """Retrieve settings from database"""
    try:
        Session = current_app.config['SESSION_FACTORY']
        session = Session()

        # Get the first settings record (or create default if none exists)
        settings_record = session.query(Settings).first()

        if not settings_record:
            # Create default settings from current config
            emotional_attrs = current_app.config.get('emotional_attributes', {})

            settings_record = Settings(
                min_grok=current_app.config.get('min_grok', 'grok-4-1-fast-non-reasoning'),
                max_grok=current_app.config.get('max_grok', 'grok-4.3'),
                emotional_attributes=json.dumps(emotional_attrs),
            )
            session.add(settings_record)
            session.commit()

        # Parse JSON fields
        emotional_attrs = json.loads(settings_record.emotional_attributes) if settings_record.emotional_attributes else {}

        settings = {
            'min_grok': settings_record.min_grok,
            'max_grok': settings_record.max_grok,
            'emotional_attributes': emotional_attrs,
        }

        session.close()
        return jsonify(settings)
    except Exception:
        current_app.logger.exception('get_settings failed')
        return jsonify({'error': 'Failed to load settings.'}), 500

@main.route('/api/settings/save', methods=['POST'])
@login_required
def save_settings():
    """Save settings to database"""
    try:
        data = request.get_json(silent=True) or {}
        Session = current_app.config['SESSION_FACTORY']
        session = Session()

        # Get or create settings record
        settings_record = session.query(Settings).first()

        if not settings_record:
            settings_record = Settings()
            session.add(settings_record)

        # Update settings
        settings_record.min_grok = data.get('min_grok', 'grok-4-1-fast-non-reasoning')
        settings_record.max_grok = data.get('max_grok', 'grok-4.3')
        settings_record.emotional_attributes = json.dumps(data.get('emotional_attributes', {}))
        settings_record.updated_at = datetime.datetime.now()

        session.commit()

        # Update app config
        current_app.config['min_grok'] = settings_record.min_grok
        current_app.config['max_grok'] = settings_record.max_grok
        current_app.config['emotional_attributes'] = json.loads(settings_record.emotional_attributes)

        session.close()
        return jsonify({'status': 'success', 'message': 'Settings saved successfully'})
    except Exception:
        current_app.logger.exception('save_settings failed')
        return jsonify({'status': 'error', 'message': 'Failed to save settings.'}), 500

@main.route('/api/seeds', methods=['GET'])
@login_required
def list_seeds():
    """Return the caller's saved seeds with their main character name and creation time.

    Filtered by ``Seed.user_id`` against the authenticated session so a
    user only ever sees their own saves. Tests build their own Flask app
    without ``LOGIN_REQUIRED`` and have no session user; the filter is
    skipped there to keep the existing fixtures working.
    """
    Session = current_app.config['SESSION_FACTORY']
    db_session = Session()
    try:
        query = db_session.query(Seed)
        if current_app.config.get('LOGIN_REQUIRED'):
            user_id = session.get('user_id')
            # Defensive: login_required already enforces session presence.
            # The == filter naturally excludes orphan (NULL user_id) rows.
            query = query.filter(Seed.user_id == user_id)
        seeds = query.order_by(Seed.created_at.desc()).all()
        result = []
        for seed in seeds:
            main_char = (
                db_session.query(Character)
                .filter(Character.seed_id == seed.id, Character.main_character == True)
                .first()
            )
            result.append({
                'seed_id': seed.id,
                'created_at': seed.created_at.isoformat() if seed.created_at else None,
                'updated_at': seed.updated_at.isoformat() if seed.updated_at else None,
                'current_turn': seed.current_turn,
                'main_character_name': main_char.name if main_char else None,
            })
        return jsonify({'seeds': result}), 200
    except Exception:
        current_app.logger.exception('list_seeds failed')
        return jsonify({'error': 'Failed to load seeds.'}), 500
    finally:
        db_session.close()


@main.route('/api/world/<int:seed_id>', methods=['GET'])
@login_required
@grok_api_key_required
def get_world(seed_id):
    """Return all world data for a given seed: locations, events, NPCs,
    main character (with stats, items, skills, statuses, relationships) and quests."""
    Session = current_app.config['SESSION_FACTORY']
    db_session = Session()
    try:
        seed = _seed_owned_by_caller(db_session, seed_id)
        if not seed:
            return jsonify({'error': 'Seed not found'}), 404

        main_character = (
            db_session.query(Character)
            .filter(Character.seed_id == seed_id, Character.main_character == True)
            .first()
        )

        # The info-panel events list is intentionally MC-centric: we only
        # surface events the protagonist actually participated in (joined via
        # EventCharacter). When no main character exists yet the list is empty.
        events = []
        if main_character:
            events = [
                {
                    'id': ev.id,
                    'name': ev.name,
                    'description': ev.description or '',
                    'type': ev.type,
                    'location_id': ev.location_id,
                    'start_turn': ev.start_turn,
                    'end_turn': ev.end_turn,
                }
                for ev in db_session.query(Event)
                .join(EventCharacter, EventCharacter.event_id == Event.id)
                .filter(Event.seed_id == seed_id,
                        EventCharacter.character_id == main_character.id)
                .distinct()
                .all()
            ]

        # Locations follow the same MC-centric rule as events: only surface
        # places where at least one MC-involved event took place. Without a
        # main character (or matching events) the list is empty.
        mc_event_location_ids = {
            ev['location_id'] for ev in events if ev['location_id'] is not None
        }
        locations = []
        if mc_event_location_ids:
            locations = [
                {
                    'id': loc.id,
                    'name': loc.name,
                    'description': loc.description or '',
                    'type': loc.type,
                    'climate': loc.climate,
                    'terrain': loc.terrain,
                    'parent_id': loc.parent_id,
                }
                for loc in db_session.query(Location)
                .filter(Location.seed_id == seed_id,
                        Location.id.in_(mc_event_location_ids))
                .all()
            ]

        # Fetch MC's outbound relationships up front so we can both annotate
        # the NPC list with acquaintance level and reuse the rows when
        # building the relationships payload below.
        mc_relationship_rows = []
        familiarity_by_npc = {}
        relationship_by_npc = {}
        if main_character:
            mc_relationship_rows = (
                db_session.query(CharacterRelationship)
                .filter(CharacterRelationship.character_id == main_character.id)
                .all()
            )
            familiarity_by_npc = {
                rel.related_character_id: (rel.familiarity or 0)
                for rel in mc_relationship_rows
            }
            relationship_by_npc = {
                rel.related_character_id: rel for rel in mc_relationship_rows
            }

        npcs = [
            _npc_payload(c, familiarity_by_npc.get(c.id, 0), relationship_by_npc.get(c.id))
            for c in db_session.query(Character)
            .filter(Character.seed_id == seed_id, Character.main_character == False)
            .all()
        ]
        # Surface known NPCs first so the accordion groups acquaintances
        # ahead of strangers without the frontend needing extra logic.
        npcs.sort(key=lambda n: (-n['familiarity'], n['name'] or ''))

        main_character_data = None
        items = []
        skills = []
        statuses = []
        relationships = []
        stats = []

        if main_character:
            main_character_data = {
                'id': main_character.id,
                'name': main_character.name,
                'race': main_character.race,
                'level': main_character.level,
                'exp_points': main_character.exp_points,
                'current_health': main_character.current_health,
                'max_health': main_character.max_health,
                'current_currency': main_character.current_currency,
            }

            stats = _build_stats(main_character)

            items = [
                {
                    'id': ci.id,
                    'name': ci.item.name if ci.item else 'Unknown Item',
                    'description': _item_description(ci),
                }
                for ci in db_session.query(CharacterItem)
                .filter(CharacterItem.character_id == main_character.id)
                .all()
            ]

            skills = [
                {
                    'id': cs.id,
                    'name': cs.skill.name if cs.skill else 'Unknown Skill',
                    'description': _skill_description(cs),
                }
                for cs in db_session.query(CharacterSkill)
                .filter(CharacterSkill.character_id == main_character.id)
                .all()
            ]

            statuses = [
                {
                    'id': cst.id,
                    'name': cst.status.name if cst.status else 'Unknown Status',
                    'description': _status_description(cst),
                }
                for cst in db_session.query(CharacterStatus)
                .filter(CharacterStatus.character_id == main_character.id)
                .all()
            ]

            relationships = [
                {
                    'id': rel.id,
                    'name': rel.related_character.name if rel.related_character else 'Unknown',
                    'description': _relationship_description(rel),
                    'familiarity': rel.familiarity or 0,
                    'acquaintance_level': _acquaintance_level(rel.familiarity),
                }
                for rel in mc_relationship_rows
            ]

        quests = [
            {
                'id': q.id,
                'name': q.name,
                'description': q.description or '',
                'currency_reward': q.currency_reward,
                'exp_reward': q.exp_reward,
            }
            for q in db_session.query(Quest).filter(Quest.seed_id == seed_id).all()
        ]

        transcript = transcript_service.list_for_seed(db_session, seed_id)

        # Resume an in-flight scenario on page load so the structured panel
        # remounts where the player left off. ``None`` means free-form turn.
        active_scenario = _scenarios.active_scenario_for(db_session, seed_id)
        scenario_view = (_scenarios.scenario_view(db_session, active_scenario)
                         if active_scenario is not None else None)

        # Phase 4 (travel): tell the frontend where the MC currently is and
        # which destinations are reachable from there with their pre-computed
        # time costs. Falls back to the seed's first top-level location for
        # legacy MC rows whose ``current_location_id`` is still NULL.
        current_loc = travel_service.resolve_current_location(
            db_session, seed_id, main_character) if main_character else None
        current_location_view = None
        destinations = []
        if current_loc is not None:
            current_location_view = {
                'id': current_loc.id, 'name': current_loc.name,
                'type': current_loc.type, 'terrain': current_loc.terrain,
                'parent_id': current_loc.parent_id,
            }
            destinations = travel_service.reachable_destinations(
                db_session, seed_id, current_loc)

        return jsonify({
            'seed_id': seed_id,
            'current_turn': seed.current_turn,
            'clock': time_service.serialize_clock(seed),
            'main_character': main_character_data,
            'locations': locations,
            'events': events,
            'characters': npcs,
            'items': items,
            'skills': skills,
            'statuses': statuses,
            'relationships': relationships,
            'stats': stats,
            'quests': quests,
            'transcript': transcript,
            'scenario': scenario_view,
            'current_location': current_location_view,
            'destinations': destinations,
        }), 200
    except Exception:
        current_app.logger.exception('get_world failed for seed_id=%s', seed_id)
        return jsonify({'error': 'Failed to load world.'}), 500
    finally:
        db_session.close()


@main.route('/api/world/<int:seed_id>/map', methods=['GET'])
@login_required
@grok_api_key_required
def get_world_map(seed_id):
    """Return the full geography for a seed: every location with its
    coordinates / parent / type, plus the inter-settlement connections.

    Unlike ``/api/world/<seed_id>``, this endpoint is NOT filtered by
    MC-event participation: the map widget shows the whole known world,
    so the player can see settlements they haven't visited yet alongside
    the ones they have. Sub-locations are returned with their parent_id
    so the frontend can drill into a settlement on click.
    """
    Session = current_app.config['SESSION_FACTORY']
    db_session = Session()
    try:
        seed = _seed_owned_by_caller(db_session, seed_id)
        if not seed:
            return jsonify({'error': 'Seed not found'}), 404

        all_locs = (
            db_session.query(Location)
            .filter(Location.seed_id == seed_id)
            .all()
        )
        locations = [
            {
                'id': loc.id,
                'name': loc.name,
                'description': loc.description or '',
                'longitude': loc.longitude,
                'latitude': loc.latitude,
                'type': loc.type,
                'climate': loc.climate,
                'terrain': loc.terrain,
                'parent_id': loc.parent_id,
            }
            for loc in all_locs
        ]

        connections = [
            {
                'id': c.id,
                'from_location_id': c.from_location_id,
                'to_location_id': c.to_location_id,
                'name': c.name or '',
                'type': c.type or 'road',
            }
            for c in db_session.query(LocationConnection)
            .filter(LocationConnection.seed_id == seed_id)
            .all()
        ]

        # Natural geography (forests, rivers, mountain ranges, lakes, ...).
        # Geometry is stored as JSON text so the frontend can hand the list
        # straight to Leaflet; a malformed row is dropped rather than 500'd
        # because the rest of the map should still render.
        features = []
        for f in (
            db_session.query(GeographicFeature)
            .filter(GeographicFeature.seed_id == seed_id)
            .all()
        ):
            try:
                points = json.loads(f.geometry) if f.geometry else []
            except (ValueError, TypeError):
                continue
            features.append({
                'id': f.id,
                'name': f.name or '',
                'type': f.type or 'forest',
                'description': f.description or '',
                'points': points,
                'closed': bool(f.closed),
            })

        return jsonify({
            'seed_id': seed_id,
            'locations': locations,
            'connections': connections,
            'features': features,
        }), 200
    except Exception:
        current_app.logger.exception('get_world_map failed for seed_id=%s', seed_id)
        return jsonify({'error': 'Failed to load world map.'}), 500
    finally:
        db_session.close()


def _build_turn_context(db_session, seed_id):
    """Collect the per-turn payload sent to the LLM.

    Returns a dict with the seed data, main character, starting + nearby
    locations and the recent transcript history (story beats only). Returns
    ``None`` when the seed has no main character or no locations yet, which
    indicates world-building hasn't finished.
    """
    main_character = (
        db_session.query(Character)
        .filter(Character.seed_id == seed_id, Character.main_character == True)
        .first()
    )
    if not main_character:
        return None

    locations = (
        db_session.query(Location)
        .filter(Location.seed_id == seed_id, Location.parent_id.is_(None))
        .all()
    )
    if not locations:
        return None

    character_payload = {
        'name': main_character.name,
        'race': main_character.race,
        'level': main_character.level,
        'current_health': main_character.current_health,
        'max_health': main_character.max_health,
    }

    def _loc(loc):
        return {
            'name': loc.name,
            'description': loc.description or '',
            'type': loc.type,
            'climate': loc.climate,
            'terrain': loc.terrain,
        }

    # The "starting_location" prompt slot historically meant locations[0];
    # with the travel system in play it now means the MC's CURRENT
    # location, falling back to locations[0] for legacy seeds whose
    # ``current_location_id`` was never set. The prompt key name is
    # preserved so existing templates keep working.
    current_loc = travel_service.resolve_current_location(
        db_session, seed_id, main_character) or locations[0]
    starting_location = _loc(current_loc)
    other_locations = [
        _loc(l) for l in locations
        if l.id != current_loc.id and l.id != getattr(current_loc, 'parent_id', None)
    ]

    # Pull the trailing transcript and drop world-building progress lines so
    # the prompt focuses on the actual story beats the player has seen.
    full_transcript = transcript_service.list_for_seed(db_session, seed_id)
    story_kinds = {
        transcript_service.KIND_NARRATION,
        transcript_service.KIND_PLAYER_INPUT,
        transcript_service.KIND_DIALOGUE,
        transcript_service.KIND_COMBAT,
        transcript_service.KIND_QUEST,
    }
    recent = [e for e in full_transcript if e['kind'] in story_kinds][-TURN_TRANSCRIPT_HISTORY:]
    transcript_lines = []
    for entry in recent:
        prefix = entry['speaker'] or entry['kind']
        transcript_lines.append(f"[{prefix}] {entry['text']}")
    transcript_text = '\n'.join(transcript_lines) if transcript_lines else '(no prior beats)'

    # Compact roster of every NPC the world already knows about. The LLM
    # uses this to (a) attribute dialogue to existing characters by their
    # canonical name and (b) decide whether a character needs to be newly
    # introduced via the 'new_characters' field of the structured response.
    npcs = (
        db_session.query(Character)
        .filter(Character.seed_id == seed_id, Character.main_character == False)
        .all()
    )
    if npcs:
        existing_lines = []
        for c in npcs:
            bits = [c.name]
            if c.race:
                bits.append(c.race)
            if c.gender is True:
                bits.append('male')
            elif c.gender is False:
                bits.append('female')
            if c.alive is False:
                bits.append('deceased')
            existing_lines.append(' - ' + ', '.join(bits))
        existing_characters_text = '\n'.join(existing_lines)
    else:
        existing_characters_text = '(none)'

    seed = db_session.query(Seed).filter(Seed.id == seed_id).first()
    world_clock = time_service.format_clock(time_service.now_world(seed))

    # Phase 5: surface a few recent off-screen events so the narrator /
    # NPCs can reference world news the player hasn't witnessed first
    # hand. Events at the MC's current location are filtered out -- the
    # player saw those play out, they're not "news from afar".
    offscreen = world_simulation.recent_offscreen_events(
        db_session, seed_id, current_loc.id, limit=4)
    if offscreen:
        ev_lines = []
        for ev in offscreen:
            loc_name = ev.location.name if ev.location is not None else '?'
            ev_lines.append(f" - {ev.name} at {loc_name}: {ev.description}")
        recent_events_text = '\n'.join(ev_lines)
    else:
        recent_events_text = '(none)'

    return {
        'seed_data': '',  # filled in by callers from request body when relevant
        'character': character_payload,
        'starting_location': starting_location,
        'other_locations': other_locations,
        'transcript': transcript_text,
        'existing_characters': existing_characters_text,
        'world_clock': world_clock or '(unset)',
        'recent_events': recent_events_text,
    }


def _make_gpt_service():
    """Build a GPTService bound to the per-request Grok API key."""
    api_key = _extract_grok_api_key()
    client = OpenAI(api_key=api_key, base_url="https://api.x.ai/v1")
    return GPTService(client, current_app.config['min_grok'])


def _generate_suggestions(gpt_service, context):
    """Ask the LLM for 4 short action suggestions; tolerate failure.

    Returns a list of suggestion strings (possibly empty). Suggestions are a
    UX nicety: callers should never abort a turn because this fails.
    """
    try:
        prompt = WORLD_BUILDING['SUGGEST_ACTIONS'].format(**context)
        text = gpt_service.get_response(prompt, json_mode=True, temperature=0.9)
        data = GPTService._parse_json_payload(text) or {}
        raw = data.get('suggestions') or []
        return [str(s).strip() for s in raw if str(s).strip()][:4]
    except Exception as e:
        print(f'Suggestion generation failed: {e}')
        return []


@main.route('/api/seed/<int:seed_id>/suggestions', methods=['GET'])
@login_required
@grok_api_key_required
@limit("60 per minute")
def get_suggestions(seed_id):
    """Return short action suggestions for the player's current situation."""
    Session = current_app.config['SESSION_FACTORY']
    db_session = Session()
    try:
        if _seed_owned_by_caller(db_session, seed_id) is None:
            return jsonify({'error': 'Seed not found'}), 404
        context = _build_turn_context(db_session, seed_id)
        if context is None:
            return jsonify({'suggestions': []}), 200
        gpt_service = _make_gpt_service()
        suggestions = _generate_suggestions(gpt_service, context)
        return jsonify({'suggestions': suggestions}), 200
    except Exception:
        current_app.logger.exception('get_suggestions failed for seed_id=%s', seed_id)
        return jsonify({'suggestions': []}), 200
    finally:
        db_session.close()


def _create_dynamic_character(db_session, seed_id, payload, *,
                              elevenlabs_api_key=None, current_dt=None,
                              name_service=None):
    """Persist a Character introduced mid-game by the LLM.

    Returns the freshly created Character on success, or ``None`` when a
    same-named character already exists for this seed (case-insensitive)
    or the name is empty. Voice assignment is best-effort: missing key /
    upstream failure leaves ``voice_id`` as NULL and the TTS layer falls
    back to the narrator voice at playback time.

    When ``name_service`` is supplied and the seed has naming themes
    assigned, the LLM-supplied name is overridden with one drawn from the
    ``NameLibrary``. This mirrors ``CharacterBuilder._persist_npc`` so
    mid-game NPCs share the same naming aesthetic as world-built ones.
    """
    llm_name = (getattr(payload, 'name', '') or '').strip()
    if not llm_name:
        return None

    # If the LLM tries to "introduce" a character whose name already
    # exists for this seed, treat it as a no-op so dialogue lines that
    # reference the existing character resolve to the canonical row.
    existing = (
        db_session.query(Character)
        .filter(Character.seed_id == seed_id,
                func.lower(Character.name) == llm_name.lower())
        .first()
    )
    if existing:
        return None

    name = llm_name
    if name_service is not None:
        try:
            themes = name_service.get_themes_for_seed(seed_id)
            if themes:
                seeded = name_service.random_name(
                    themes, gender=payload.gender, category='first')
                if seeded:
                    name = seeded
        except Exception as e:
            print(f"Library name lookup failed for dynamic "
                  f"character '{llm_name}': {e}")

    # The library is a finite pool, so a popular theme can produce
    # collisions across consecutive turns; fall back to the LLM name when
    # the override would clash with an existing character on this seed.
    if name != llm_name:
        collision = (
            db_session.query(Character)
            .filter(Character.seed_id == seed_id,
                    func.lower(Character.name) == name.lower())
            .first()
        )
        if collision:
            name = llm_name

    voice_id = None
    if elevenlabs_api_key:
        try:
            voice_id = elevenlabs_service.find_voice_for_character(
                elevenlabs_api_key,
                gender=payload.gender,
                date_of_birth=payload.date_of_birth,
                current_dt=current_dt,
                race=payload.race,
                search_text=getattr(payload, 'description', None) or None,
            )
        except Exception as e:
            print(f"Voice search failed for dynamic character '{name}': {e}")

    # Light-weight stat block mirroring CharacterBuilder._persist_npc so
    # ad-hoc characters can participate in the same combat / progression
    # systems as world-built NPCs without a second pass.
    level = random.randint(1, 3)
    char = Character(
        seed_id=seed_id,
        main_character=False,
        alive=True,
        name=name,
        date_of_birth=payload.date_of_birth,
        race=payload.race,
        gender=payload.gender,
        level=level,
        exp_points=100 * ((2 ** (level - 1)) - 1),
        strength=random.randint(4, 16) + level,
        speed=random.randint(4, 16) + level,
        agility=random.randint(4, 16) + level,
        intelligence=random.randint(4, 16) + level,
        wisdom=random.randint(4, 16) + level,
        charisma=random.randint(4, 16) + level,
        current_health=100 * level,
        max_health=100 * level,
        current_currency=random.randint(0, 50),
        voice_id=voice_id,
    )
    db_session.add(char)
    db_session.commit()
    db_session.refresh(char)

    # Seed an MC <-> NPC acquaintance row so the new character isn't read
    # back as an "unknown" stranger by the world payload (familiarity == 0
    # is reserved for that). Mirrors the defaults used by
    # CharacterBuilder.create_main_character_relationships at world build
    # time but with neutral stats and familiarity=1 to reflect that the
    # two have only just met. ``== True`` matches the comparison the rest
    # of the codebase (and the MC insert path) uses; ``is_(True)`` would
    # generate ``IS TRUE`` SQL which is not portable across every backend
    # the app may run on. Failure here is non-fatal: the character itself
    # is already persisted and gameplay can continue, but we log the
    # traceback so the silent dropout shows up in the server logs.
    try:
        mc = (
            db_session.query(Character)
            .filter(Character.seed_id == seed_id,
                    Character.main_character == True)  # noqa: E712
            .first()
        )
        if mc is None:
            current_app.logger.warning(
                "Skipping MC relationship for dynamic character '%s' on "
                "seed %s: main character row not found.", name, seed_id)
        elif mc.id != char.id:
            db_session.add(CharacterRelationship(
                seed_id=seed_id,
                character_id=mc.id,
                related_character_id=char.id,
                relationship_type='acquaintance',
                attraction=5, respect=5, trust=5,
                familiarity=1, anger=5, fear=5,
            ))
            db_session.commit()
    except Exception:
        db_session.rollback()
        current_app.logger.exception(
            "Failed to seed MC relationship for dynamic character '%s' "
            "on seed %s.", name, seed_id)

    return char


def _split_paragraphs(text):
    """Break narrator prose into paragraph chunks.

    Each paragraph is persisted (and TTS'd) as its own transcript entry so
    the audio for a multi-paragraph beat starts playing as soon as the
    first paragraph is rendered, rather than waiting for ElevenLabs to
    synthesize the whole block. Splitting on blank lines matches how the
    LLM is asked to format narration in the prompt templates.
    """
    if not text:
        return []
    parts = [p.strip() for p in re.split(r'\n\s*\n', text)]
    return [p for p in parts if p]


def _resolve_dialogue_speaker(db_session, seed_id, raw_speaker, name_lookup):
    """Map a dialogue speaker string to its canonical Character.name.

    The LLM occasionally drops case or trailing whitespace; ``name_lookup``
    is a {lower(name): canonical_name} dict prebuilt from the seed's
    characters (including ones created earlier in this turn) so we don't
    re-query the DB per line. Falls back to the trimmed raw value when
    nothing matches; the TTS layer will then default to the narrator voice.
    """
    cleaned = (raw_speaker or '').strip()
    if not cleaned:
        return ''
    return name_lookup.get(cleaned.lower(), cleaned)


def _arbiter_adjudicate(gpt_service, context):
    """Run the Arbiter ruling pass on the player's action.

    Returns ``(ruling, error)`` where ``ruling`` is an
    ``ActionAdjudicationOut`` (or a fallback no-check ruling on parse
    failure) and ``error`` is a human string when the call itself blew
    up. Failure is non-fatal: the turn proceeds as a free auto-success
    when the Arbiter cannot be reached so a flaky model does not strand the
    player.
    """
    try:
        prompt = ARBITER_ADJUDICATE.format(**context)
        ruling = gpt_service.get_structured(
            prompt, ActionAdjudicationOut,
            max_attempts=2, temperature=0.3,
        )
    except Exception as e:
        return _default_ruling(), str(e)
    if ruling is None:
        return _default_ruling(), 'Arbiter returned an invalid payload.'
    return ruling, None


def _default_ruling():
    """Auto-success ruling used when the Arbiter call fails or is skipped."""
    return ActionAdjudicationOut(
        requires_check=False, ability='strength', dc=10,
        proficient=False, advantage=False, disadvantage=False,
        time_cost_minutes=time_service.DEFAULT_TURN_MINUTES, reason='',
    )


def _resolve_check(db_session, seed_id, character, ruling, *,
                   session_factory, current_turn):
    """Roll the check the Arbiter asked for and persist Arbiter + dice transcript lines.

    Returns ``(check_result, transcript_entries)`` where ``check_result``
    is a ``CheckResult`` (or ``None`` when the ruling didn't ask for a
    check) and ``transcript_entries`` is the JSON-friendly list of new
    entries to surface to the frontend in the turn response.
    """
    entries = []
    # Arbiter ruling line first so the player sees WHY a roll is happening
    # before they see the dice land. Empty reasons are skipped.
    reason = (ruling.reason or '').strip()
    if reason:
        arbiter_entry = transcript_service.add_entry(
            session_factory, seed_id,
            transcript_service.KIND_ARBITER, reason,
            turn=current_turn, speaker='Arbiter',
            meta={
                'requires_check': bool(ruling.requires_check),
                'ability': ruling.ability,
                'dc': int(ruling.dc),
                'proficient': bool(ruling.proficient),
                'advantage': bool(ruling.advantage),
                'disadvantage': bool(ruling.disadvantage),
                'time_cost_minutes': int(ruling.time_cost_minutes or 0),
            },
        )
        if arbiter_entry is not None:
            entries.append({
                'id': arbiter_entry.id,
                'kind': transcript_service.KIND_ARBITER,
                'speaker': 'Arbiter',
                'text': reason,
            })
    if not ruling.requires_check or character is None:
        return None, entries

    result = dice_service.perform_check(
        character, ruling.ability, ruling.dc,
        proficient=ruling.proficient,
        advantage=ruling.advantage, disadvantage=ruling.disadvantage,
        description=reason,
    )
    line = dice_service.format_check(result)
    dice_entry = transcript_service.add_entry(
        session_factory, seed_id,
        transcript_service.KIND_DICE, line,
        turn=current_turn, speaker='Arbiter',
        meta=result.to_meta(),
    )
    if dice_entry is not None:
        entries.append({
            'id': dice_entry.id,
            'kind': transcript_service.KIND_DICE,
            'speaker': 'Arbiter',
            'text': line,
            # Forward the structured roll so the frontend can flash the
            # d20 overlay with the actual face / verdict / colour ramp
            # without having to re-parse ``line``.
            'meta': result.to_meta(),
        })
    return result, entries


def _format_arbiter_outcome(ruling, check_result):
    """Render the Arbiter ruling + dice verdict as a short brief for the narrator.

    Kept terse so the narration prompt stays cheap. The narrator reads
    this and is instructed to honour the verdict verbatim -- no second
    rolls, no overturning a failure into a success.
    """
    if ruling is None:
        return ('No check required; the action proceeds as declared. '
                'Narrate the outcome as a routine success.')
    if not ruling.requires_check or check_result is None:
        cost = int(ruling.time_cost_minutes or 0)
        cost_bit = f' (~{cost} minutes elapse)' if cost else ''
        return ('No check required; the action proceeds as declared'
                f'{cost_bit}. Narrate the outcome as a routine success.')
    bits = [
        f"Ability: {check_result.ability.title()}",
        f"DC: {check_result.dc}",
        f"d20: {check_result.raw_d20}",
        f"Total: {check_result.total}",
    ]
    if check_result.critical_success:
        verdict = 'CRITICAL SUCCESS -- describe an unusually clean, lucky outcome.'
    elif check_result.critical_failure:
        verdict = ('CRITICAL FAILURE -- describe a notably bad outcome '
                   '(complication, fumble, or worse).')
    elif check_result.success:
        verdict = 'SUCCESS -- the action lands as intended.'
    else:
        verdict = ('FAILURE -- the action does not work; describe the '
                   'attempt and the consequence, but do NOT claim success.')
    cost = int(ruling.time_cost_minutes or 0)
    if cost:
        bits.append(f"Time elapsed: ~{cost} minutes")
    return ' | '.join(bits) + '\n' + verdict


@main.route('/api/seed/<int:seed_id>/turn', methods=['POST'])
@login_required
@grok_api_key_required
@limit("60 per minute")
def submit_turn(seed_id):
    """Accept a player action, persist it, and return the turn's transcript.

    Persists the player's input as a ``player_input`` entry, asks the LLM
    for a structured continuation (narration prose, per-character dialogue
    lines, and any newly introduced characters), persists each piece as a
    separate transcript entry attributed to the right speaker, and returns
    the full ordered batch of new entries alongside a fresh suggestion list.
    """
    data = request.get_json(silent=True) or {}
    action = (data.get('action') or '').strip()
    seed_data = data.get('seed_data') or ''

    if not action:
        return jsonify({'success': False, 'message': 'Action is required.'}), 400

    Session = current_app.config['SESSION_FACTORY']
    session_factory = Session
    db_session = Session()

    try:
        seed = _seed_owned_by_caller(db_session, seed_id)
        if not seed:
            return jsonify({'success': False, 'message': 'Seed not found.'}), 404

        # If a scenario is in flight, the free-form turn loop is gated --
        # the player must drive it to a resolution (or abort it) through
        # the scenario routes before the narrator picks up the story.
        active = _scenarios.active_scenario_for(db_session, seed_id)
        if active is not None:
            return jsonify({
                'success': False,
                'message': 'A scenario is in progress; resolve it first.',
                'scenario': _scenarios.scenario_view(db_session, active),
            }), 409

        turn = seed.current_turn or 1

        # Persist the player's input first so it shows up in the transcript
        # even if the LLM call below fails.
        transcript_service.add_entry(
            session_factory, seed_id,
            transcript_service.KIND_PLAYER_INPUT, action,
            turn=turn, speaker='You',
        )

        context = _build_turn_context(db_session, seed_id)
        if context is None:
            return jsonify({
                'success': False,
                'message': 'World is not ready yet; finish world building first.'
            }), 409
        context['seed_data'] = seed_data
        context['player_action'] = action

        gpt_service = _make_gpt_service()

        # Phase 3: Arbiter adjudication runs FIRST. The Arbiter picks an
        # ability + DC + time cost, the substrate rolls the dice
        # deterministically, and the verdict is then handed to the narrator
        # so the prose always matches the mechanical result. Both the
        # Arbiter ruling line and the dice roll land in the transcript ahead
        # of the narration so the player sees them in the order they happened.
        ruling, ruling_err = _arbiter_adjudicate(gpt_service, context)
        if ruling_err:
            current_app.logger.warning(
                "Arbiter adjudication fell back to auto-success on seed %s: %s",
                seed_id, ruling_err,
            )
        main_character = (
            db_session.query(Character)
            .filter(Character.seed_id == seed_id,
                    Character.main_character == True)  # noqa: E712
            .first()
        )
        check_result, arbiter_entries = _resolve_check(
            db_session, seed_id, main_character, ruling,
            session_factory=session_factory, current_turn=turn,
        )
        context['arbiter_outcome'] = _format_arbiter_outcome(ruling, check_result)

        narration_prompt = WORLD_BUILDING['CONTINUE_NARRATIVE'].format(**context)
        try:
            turn_payload = gpt_service.get_structured(
                narration_prompt, TurnResponseOut,
                max_attempts=2, temperature=1.0,
            )
        except Exception as e:
            return jsonify({'success': False, 'message': f'Narration failed: {e}'}), 502
        if turn_payload is None:
            return jsonify({'success': False,
                            'message': 'Narration failed: invalid LLM payload.'}), 502

        narration = (turn_payload.narration or '').strip()
        # Arbiter ruling + dice entries come first so they precede the
        # narration in the transcript order the frontend renders.
        entries = list(arbiter_entries)

        # 1) Newly introduced characters land first so dialogue lines that
        # reference them resolve to the correct (just-created) Character row.
        # ``name_service`` lets the dynamic-character path draw names from
        # the seed's NameLibrary subset, mirroring world-build behavior so
        # mid-game NPCs share the same naming aesthetic as their peers.
        elevenlabs_api_key = _extract_elevenlabs_api_key()
        current_dt = seed.current_date_time
        name_service = NameService(db_session)
        created_characters = []
        # Track the LLM-supplied names alongside each persisted Character so
        # dialogue lines that reference the original name still resolve to
        # the (possibly renamed) row's canonical name.
        for new_char in turn_payload.new_characters:
            original_name = (getattr(new_char, 'name', '') or '').strip()
            char = _create_dynamic_character(
                db_session, seed_id, new_char,
                elevenlabs_api_key=elevenlabs_api_key,
                current_dt=current_dt,
                name_service=name_service,
            )
            if char is not None:
                created_characters.append((char, original_name))

        # Build a lookup of every character name in this seed (including
        # the MC and the ones created above) so dialogue speakers map to
        # canonical names. The first-name alias catches the common LLM
        # habit of calling characters by their given name only, which
        # would otherwise leave the TTS layer unable to resolve a voice.
        name_lookup = {}
        for c in (
            db_session.query(Character)
            .filter(Character.seed_id == seed_id)
            .all()
        ):
            full = (c.name or '').strip()
            if not full:
                continue
            name_lookup[full.lower()] = full
            first = full.split()[0].lower()
            name_lookup.setdefault(first, full)

        # When a dynamic character was renamed by the library override,
        # alias the LLM-supplied name (and its first-name token) so the
        # dialogue lines the LLM produced for the original name still map
        # to the persisted character.
        for char, original_name in created_characters:
            if not original_name:
                continue
            if original_name.lower() == (char.name or '').lower():
                continue
            name_lookup.setdefault(original_name.lower(), char.name)
            original_first = original_name.split()[0].lower() if original_name else ''
            if original_first:
                name_lookup.setdefault(original_first, char.name)

        # 2) Narrator prose (if any). Each paragraph lands as its own entry
        # so TTS playback can stream paragraph-by-paragraph instead of
        # blocking on a single long synthesis call.
        for paragraph in _split_paragraphs(narration):
            narration_entry = transcript_service.add_entry(
                session_factory, seed_id,
                transcript_service.KIND_NARRATION, paragraph,
                turn=turn, speaker='Narrator',
            )
            if narration_entry is not None:
                entries.append({
                    'id': narration_entry.id,
                    'kind': transcript_service.KIND_NARRATION,
                    'speaker': 'Narrator',
                    'text': paragraph,
                })

        # 3) Per-character dialogue lines, attributed individually so the
        # frontend renders + voices each one as the speaking character.
        for line in turn_payload.dialogue:
            text = (line.text or '').strip()
            if not text:
                continue
            speaker = _resolve_dialogue_speaker(
                db_session, seed_id, line.speaker, name_lookup,
            )
            dialogue_entry = transcript_service.add_entry(
                session_factory, seed_id,
                transcript_service.KIND_DIALOGUE, text,
                turn=turn, speaker=speaker or None,
            )
            if dialogue_entry is not None:
                entries.append({
                    'id': dialogue_entry.id,
                    'kind': transcript_service.KIND_DIALOGUE,
                    'speaker': speaker,
                    'text': text,
                })

        # Bump the turn counter only after a successful narration so failed
        # turns can be retried without skipping ahead. The world clock
        # advances by the Arbiter's adjudicated cost (the Arbiter is the
        # source of truth for time); the narrator no longer estimates time
        # itself. Falls back to the default per-turn pace if the Arbiter's
        # value is missing or non-positive so the clock keeps ticking either way.
        seed.current_turn = turn + 1
        time_cost = int(getattr(ruling, 'time_cost_minutes', 0) or 0)
        if time_cost <= 0:
            time_cost = time_service.DEFAULT_TURN_MINUTES
        time_service.advance_time(db_session, seed, time_cost)
        seed.updated_at = datetime.datetime.now()
        db_session.commit()

        # Phase 5: now that the world clock has advanced, give the
        # autonomous simulator a chance to draft fresh off-screen news.
        # ``maybe_simulate`` is a no-op when not enough in-world time has
        # elapsed, so a flurry of one-minute beats won't stack up calls.
        # Failures are swallowed inside the service; any persisted news
        # gets appended to the transcript entries the player sees this
        # turn so the response carries the gossip with it.
        try:
            sim_context = _build_turn_context(db_session, seed_id) or context
            sim_context['seed_data'] = seed_data
            _, sim_entries = world_simulation.maybe_simulate(
                db_session, seed_id, gpt_service,
                context=sim_context, session_factory=session_factory,
            )
            if sim_entries:
                entries.extend(sim_entries)
        except Exception as e:
            current_app.logger.warning(
                "world_simulation tick failed on seed %s: %s", seed_id, e)

        # If the narrator asked to hand off to a structured scenario, spin
        # one up now so the response carries it back to the client. Failures
        # here are non-fatal: the standard turn already landed, the player
        # just gets the trigger as a hint to retry / pick differently.
        scenario_view = None
        trigger = getattr(turn_payload, 'scenario_trigger', None)
        if trigger is not None:
            handler = _scenarios.get_handler(trigger.kind)
            if handler is not None:
                try:
                    started = handler.start(
                        db_session, seed_id, trigger,
                        current_turn=seed.current_turn,
                        session_factory=session_factory,
                        gpt_service=gpt_service,
                    )
                except Exception as e:
                    db_session.rollback()
                    started = None
                    print(f"Scenario start failed for kind '{trigger.kind}': {e}")
                if started is not None:
                    scenario_view = _scenarios.scenario_view(db_session, started)

        # Refresh suggestions against the now-updated transcript so the next
        # batch reflects what just happened. Failure here is non-fatal.
        # Skip suggestions while a scenario is active -- the player's next
        # input must go through the scenario action endpoint, not a free
        # action prompt.
        suggestions = []
        if scenario_view is None:
            refreshed_context = _build_turn_context(db_session, seed_id) or context
            refreshed_context['seed_data'] = seed_data
            suggestions = _generate_suggestions(gpt_service, refreshed_context)

        # ``narration`` / ``narration_id`` are kept for backwards compatibility
        # with older frontends; new code should iterate ``entries`` instead.
        narration_id = next(
            (e['id'] for e in entries
             if e['kind'] == transcript_service.KIND_NARRATION),
            None,
        )
        return jsonify({
            'success': True,
            'narration': narration,
            'narration_id': narration_id,
            'entries': entries,
            'new_characters': [
                {'id': c.id, 'name': c.name, 'race': c.race}
                for c, _ in created_characters
            ],
            'suggestions': suggestions,
            'turn': seed.current_turn,
            'clock': time_service.serialize_clock(seed),
            'scenario': scenario_view,
        }), 200
    except Exception:
        db_session.rollback()
        current_app.logger.exception('submit_turn failed for seed_id=%s', seed_id)
        return jsonify({'success': False, 'message': 'Turn failed; please retry.'}), 500
    finally:
        db_session.close()


@main.route('/api/seed/<int:seed_id>/travel', methods=['POST'])
@login_required
@grok_api_key_required
@limit("60 per minute")
def submit_travel(seed_id):
    """Move the main character to a reachable destination, advancing the clock.

    Body: ``{"destination_id": <int>}``. The destination must appear in
    ``travel_service.reachable_destinations`` for the MC's current
    location -- arbitrary teleporting is rejected. The world clock is
    bumped by the deterministic ``travel_minutes`` cost (no arbiter call,
    no narration round-trip; this is the cheap, predictable path), and
    a transcript line lands describing the trip so the next narration /
    suggestion call has the move in its history.
    """
    data = request.get_json(silent=True) or {}
    try:
        dest_id = int(data.get('destination_id'))
    except (TypeError, ValueError):
        return jsonify({'success': False,
                        'message': 'destination_id is required.'}), 400

    Session = current_app.config['SESSION_FACTORY']
    session_factory = Session
    db_session = Session()
    try:
        seed = _seed_owned_by_caller(db_session, seed_id)
        if not seed:
            return jsonify({'success': False, 'message': 'Seed not found.'}), 404

        # A scenario locks the free-text loop; travel goes through that
        # gate too so the player can't walk away mid-battle / mid-trade.
        active = _scenarios.active_scenario_for(db_session, seed_id)
        if active is not None:
            return jsonify({
                'success': False,
                'message': 'A scenario is in progress; resolve it first.',
                'scenario': _scenarios.scenario_view(db_session, active),
            }), 409

        mc = (
            db_session.query(Character)
            .filter(Character.seed_id == seed_id,
                    Character.main_character == True)  # noqa: E712
            .first()
        )
        if mc is None:
            return jsonify({'success': False,
                            'message': 'No main character on this seed.'}), 409

        from_loc = travel_service.resolve_current_location(
            db_session, seed_id, mc)
        if from_loc is None:
            return jsonify({'success': False,
                            'message': 'No starting location on this seed.'}), 409

        destinations = travel_service.reachable_destinations(
            db_session, seed_id, from_loc)
        match = next((d for d in destinations if d['id'] == dest_id), None)
        if match is None:
            return jsonify({'success': False,
                            'message': 'Destination is not reachable from here.'}), 409

        to_loc = (db_session.query(Location)
                  .filter(Location.id == dest_id).first())
        if to_loc is None:
            return jsonify({'success': False,
                            'message': 'Destination not found.'}), 404

        minutes = int(match['minutes'] or 0)
        mc.current_location_id = to_loc.id
        time_service.advance_time(db_session, seed, minutes)
        seed.updated_at = datetime.datetime.now()

        # Transcript line + structured meta so the prompt history reflects
        # the move and the frontend can render a distinct "travel" pill.
        line = (f"You travel from {from_loc.name} to {to_loc.name} "
                f"(~{minutes} minutes).")
        travel_entry = transcript_service.add_entry(
            session_factory, seed_id,
            transcript_service.KIND_NARRATION, line,
            turn=seed.current_turn or 1, speaker='Narrator',
            meta={
                'kind': 'travel',
                'from_id': from_loc.id, 'from_name': from_loc.name,
                'to_id': to_loc.id, 'to_name': to_loc.name,
                'minutes': minutes,
            },
        )
        db_session.commit()

        entries = []
        if travel_entry is not None:
            entries.append({
                'id': travel_entry.id,
                'kind': transcript_service.KIND_NARRATION,
                'speaker': 'Narrator',
                'text': line,
            })

        # Phase 5: travel typically advances the clock by hours, which is
        # almost always enough to trigger an autonomous-events tick. We
        # build the context AFTER the move so the simulator sees the new
        # location as the MC's "starting_location" (and excludes it from
        # off-screen candidates). Failures are non-fatal -- the trip
        # already landed.
        try:
            sim_context = _build_turn_context(db_session, seed_id)
            if sim_context is not None:
                gpt_service = _make_gpt_service()
                _, sim_entries = world_simulation.maybe_simulate(
                    db_session, seed_id, gpt_service,
                    context=sim_context, session_factory=session_factory,
                )
                if sim_entries:
                    entries.extend(sim_entries)
        except Exception as e:
            current_app.logger.warning(
                "world_simulation tick failed on travel for seed %s: %s",
                seed_id, e)

        # Refresh the destinations list for the new location so the UI
        # can repopulate its travel panel without a second round-trip.
        new_destinations = travel_service.reachable_destinations(
            db_session, seed_id, to_loc)

        return jsonify({
            'success': True,
            'minutes': minutes,
            'clock': time_service.serialize_clock(seed),
            'current_location': {
                'id': to_loc.id, 'name': to_loc.name,
                'type': to_loc.type, 'terrain': to_loc.terrain,
                'parent_id': to_loc.parent_id,
            },
            'destinations': new_destinations,
            'entries': entries,
        }), 200
    except Exception:
        db_session.rollback()
        current_app.logger.exception('submit_travel failed for seed_id=%s', seed_id)
        return jsonify({'success': False, 'message': 'Travel failed; please retry.'}), 500
    finally:
        db_session.close()


@main.route('/api/seed/<int:seed_id>/scenario', methods=['GET'])
@login_required
@grok_api_key_required
@limit("60 per minute")
def get_active_scenario(seed_id):
    """Return the seed's currently-active scenario view, or ``null``.

    The frontend hits this on resume to decide whether to mount the
    scenario panel; it's also handy after a refresh to recover state
    without re-fetching the whole world payload.
    """
    Session = current_app.config['SESSION_FACTORY']
    db_session = Session()
    try:
        if _seed_owned_by_caller(db_session, seed_id) is None:
            return jsonify({'scenario': None, 'error': 'Seed not found'}), 404
        active = _scenarios.active_scenario_for(db_session, seed_id)
        if active is None:
            return jsonify({'scenario': None}), 200
        return jsonify({
            'scenario': _scenarios.scenario_view(db_session, active),
        }), 200
    except Exception:
        current_app.logger.exception('get_active_scenario failed for seed_id=%s', seed_id)
        return jsonify({'scenario': None}), 200
    finally:
        db_session.close()


@main.route('/api/seed/<int:seed_id>/scenario/<int:scenario_id>/action',
            methods=['POST'])
@login_required
@grok_api_key_required
@limit("120 per minute")
def submit_scenario_action(seed_id, scenario_id):
    """Apply a structured action to an active scenario.

    Routes the incoming JSON body to the kind-specific handler in
    ``app/scenarios``; the handler is responsible for persisting state /
    transcript writes and returning the refreshed view, any new transcript
    entries to render, and a resolution flag.
    """
    payload = request.get_json(silent=True) or {}
    Session = current_app.config['SESSION_FACTORY']
    session_factory = Session
    db_session = Session()
    try:
        if _seed_owned_by_caller(db_session, seed_id) is None:
            return jsonify({'success': False,
                            'message': 'Scenario not found.'}), 404
        scenario = (
            db_session.query(Scenario)
            .filter(Scenario.id == scenario_id, Scenario.seed_id == seed_id)
            .first()
        )
        if scenario is None:
            return jsonify({'success': False,
                            'message': 'Scenario not found.'}), 404
        if scenario.status != 'active':
            return jsonify({'success': False,
                            'message': 'Scenario is no longer active.',
                            'view': _scenarios.scenario_view(db_session, scenario),
                            }), 409

        handler = _scenarios.get_handler(scenario.kind)
        if handler is None:
            return jsonify({'success': False,
                            'message': f"No handler for kind '{scenario.kind}'."
                            }), 500

        seed = db_session.query(Seed).filter(Seed.id == seed_id).first()
        current_turn = seed.current_turn if seed else None

        gpt_service = _make_gpt_service()
        result, status = handler.apply_action(
            db_session, scenario, payload,
            gpt_service=gpt_service, session_factory=session_factory,
            current_turn=current_turn,
        )
        if status >= 400:
            return jsonify({'success': False, **result}), status
        return jsonify({'success': True, **result}), 200
    except Exception:
        db_session.rollback()
        current_app.logger.exception(
            'submit_scenario_action failed for seed_id=%s scenario_id=%s',
            seed_id, scenario_id)
        return jsonify({'success': False, 'message': 'Scenario action failed; please retry.'}), 500
    finally:
        db_session.close()


@main.route('/api/seed/<int:seed_id>/scenario/<int:scenario_id>/abort',
            methods=['POST'])
@login_required
@grok_api_key_required
@limit("30 per minute")
def abort_scenario(seed_id, scenario_id):
    """Force-close an active scenario with status='aborted'.

    Used by the frontend's "Leave" / panel-dismiss control when the
    scenario doesn't expose a graceful exit verb (or when the player wants
    to bail out mid-flow). Persistent state changes already committed by
    earlier actions are kept; only the active flag flips off.
    """
    Session = current_app.config['SESSION_FACTORY']
    session_factory = Session
    db_session = Session()
    try:
        if _seed_owned_by_caller(db_session, seed_id) is None:
            return jsonify({'success': False,
                            'message': 'Scenario not found.'}), 404
        scenario = (
            db_session.query(Scenario)
            .filter(Scenario.id == scenario_id, Scenario.seed_id == seed_id)
            .first()
        )
        if scenario is None:
            return jsonify({'success': False,
                            'message': 'Scenario not found.'}), 404
        if scenario.status != 'active':
            return jsonify({'success': True,
                            'view': _scenarios.scenario_view(db_session, scenario),
                            }), 200

        seed = db_session.query(Seed).filter(Seed.id == seed_id).first()
        current_turn = seed.current_turn if seed else None
        summary = scenario.summary or f'{scenario.kind.capitalize()} aborted.'
        from app.scenarios.base import resolve as _resolve
        _resolve(db_session, scenario, 'aborted', summary,
                 current_turn=current_turn)
        db_session.commit()
        transcript_service.add_entry(
            session_factory, seed_id,
            transcript_service.KIND_SYSTEM, summary,
            turn=current_turn, speaker='Narrator',
            meta={'scenario_kind': scenario.kind, 'verb': 'abort',
                  'scenario_id': scenario.id},
        )
        return jsonify({'success': True,
                        'view': _scenarios.scenario_view(db_session, scenario),
                        }), 200
    except Exception:
        db_session.rollback()
        current_app.logger.exception(
            'abort_scenario failed for seed_id=%s scenario_id=%s',
            seed_id, scenario_id)
        return jsonify({'success': False, 'message': 'Failed to abort scenario.'}), 500
    finally:
        db_session.close()



def _character_description(c, acquaintance_level='close'):
    # Unknown NPCs only expose that they are deceased (visible at a glance);
    # race and level read like privileged knowledge the MC shouldn't have
    # for a stranger they've never met.
    if acquaintance_level == 'unknown':
        return 'Deceased' if c.alive is False else 'An unfamiliar face.'
    parts = []
    if c.race:
        parts.append(f"Race: {c.race}")
    if c.level is not None and acquaintance_level in ('acquainted', 'close'):
        parts.append(f"Level: {c.level}")
    if c.alive is False:
        parts.append("Deceased")
    return ', '.join(parts) if parts else ''


# Familiarity buckets shared by /api/world response and the per-NPC payload.
# Mirrors the semantics described for the seeded MC<->NPC relationships:
#   0      -> never met
#   1-3    -> seen / heard of
#   4-7    -> knows them
#   8-10   -> close
def _acquaintance_level(familiarity):
    f = familiarity or 0
    if f <= 0:
        return 'unknown'
    if f <= 3:
        return 'seen'
    if f <= 7:
        return 'acquainted'
    return 'close'


_ACQUAINTANCE_ICONS = {
    'unknown': '❓',
    'seen': '👁️',
    'acquainted': '👤',
    'close': '💖',
}


def _npc_payload(c, familiarity, relationship=None):
    level = _acquaintance_level(familiarity)
    display_name = 'Stranger' if level == 'unknown' else c.name
    # Strangers haven't been "met" from the MC's POV, so suppress the
    # emotional readout even when a CharacterRelationship row exists.
    relationship_payload = None
    if relationship is not None and level != 'unknown':
        relationship_payload = {
            'relationship_type': relationship.relationship_type,
            'attraction': relationship.attraction,
            'respect': relationship.respect,
            'trust': relationship.trust,
            'familiarity': relationship.familiarity,
            'anger': relationship.anger,
            'fear': relationship.fear,
            'description': _relationship_description(relationship),
        }
    return {
        'id': c.id,
        'name': c.name,
        'display_name': display_name,
        'display_icon': _ACQUAINTANCE_ICONS[level],
        'description': _character_description(c, level),
        'race': c.race,
        'level': c.level,
        'alive': c.alive,
        'familiarity': familiarity,
        'acquaintance_level': level,
        'relationship': relationship_payload,
    }


def _item_description(ci):
    parts = []
    if ci.item and ci.item.description:
        parts.append(ci.item.description)
    if ci.quantity is not None:
        parts.append(f"Quantity: {ci.quantity}")
    if ci.condition is not None:
        parts.append(f"Condition: {ci.condition}")
    return ' — '.join(parts) if parts else ''


def _skill_description(cs):
    parts = []
    if cs.skill and cs.skill.description:
        parts.append(cs.skill.description)
    if cs.level is not None:
        parts.append(f"Level: {cs.level}")
    return ' — '.join(parts) if parts else ''


def _status_description(cst):
    parts = []
    if cst.status and cst.status.description:
        parts.append(cst.status.description)
    if cst.active is not None:
        parts.append(f"Active: {bool(cst.active)}")
    return ' — '.join(parts) if parts else ''


def _relationship_description(rel):
    parts = []
    if rel.relationship_type:
        parts.append(f"Type: {rel.relationship_type}")
    parts.append(
        f"Attraction: {rel.attraction}, Respect: {rel.respect}, "
        f"Trust: {rel.trust}, Familiarity: {rel.familiarity}, "
        f"Anger: {rel.anger}, Fear: {rel.fear}"
    )
    return ' — '.join(parts)


def _build_stats(c):
    fields = [
        ('strength', 'Strength'),
        ('speed', 'Speed'),
        ('agility', 'Agility'),
        ('intelligence', 'Intelligence'),
        ('wisdom', 'Wisdom'),
        ('charisma', 'Charisma'),
        ('current_health', 'Current Health'),
        ('max_health', 'Max Health'),
        ('current_currency', 'Currency'),
        ('level', 'Level'),
        ('exp_points', 'Experience'),
    ]
    return [
        {'id': key, 'name': label, 'description': str(getattr(c, key))}
        for key, label in fields
        if getattr(c, key) is not None
    ]


@main.route('/auth/signup', methods=['POST'])
@limit("5 per hour")
def signup():
    data = request.get_json(silent=True) or {}
    username = data.get('username')
    email = data.get('email')
    password = data.get('password')

    if not username or not email or not password:
        return jsonify({'success': False, 'message': 'All fields are required'}), 400

    Session = current_app.config['SESSION_FACTORY']
    db_session = Session()

    try:
        # Check if user already exists
        existing_user = db_session.query(User).filter(
            (User.username == username) | (User.email == email)
        ).first()

        if existing_user:
            return jsonify({'success': False, 'message': 'Username or email already exists'}), 400

        # Create new user
        password_hash = generate_password_hash(password)
        new_user = User(username=username, email=email, password_hash=password_hash)
        db_session.add(new_user)
        db_session.commit()

        # Set session
        session['user_id'] = new_user.id
        session['username'] = new_user.username

        return jsonify({'success': True, 'message': 'Account created successfully', 'username': username}), 201
    except Exception:
        db_session.rollback()
        current_app.logger.exception('signup failed')
        return jsonify({'success': False, 'message': 'Could not create account.'}), 500
    finally:
        db_session.close()

@main.route('/auth/login', methods=['POST'])
@limit("20 per minute")
def login():
    data = request.get_json(silent=True) or {}
    username = data.get('username')
    password = data.get('password')
    remember = bool(data.get('remember'))

    if not username or not password:
        return jsonify({'success': False, 'message': 'Username and password are required'}), 400

    Session = current_app.config['SESSION_FACTORY']
    db_session = Session()

    try:
        user = db_session.query(User).filter(User.username == username).first()

        if not user or not check_password_hash(user.password_hash, password):
            return jsonify({'success': False, 'message': 'Invalid username or password'}), 401

        # Set session. When the caller opted in to "Stay signed in", mark the
        # session permanent so it survives browser restarts up to
        # PERMANENT_SESSION_LIFETIME instead of expiring with the browser.
        session['user_id'] = user.id
        session['username'] = user.username
        session.permanent = remember

        return jsonify({'success': True, 'message': 'Login successful', 'username': username}), 200
    except Exception:
        current_app.logger.exception('login failed')
        return jsonify({'success': False, 'message': 'Login failed.'}), 500
    finally:
        db_session.close()

@main.route('/auth/logout', methods=['POST'])
def logout():
    # Drop the encrypted Grok key bound to this user before clearing the
    # session: once the session is cleared we lose the user_id needed to
    # locate the entry, leaving a stale blob in the store until its TTL.
    user_id = session.get('user_id')
    if user_id is not None:
        _grok_key_store.clear(user_id)
        _elevenlabs_key_store.clear(user_id)
    session.clear()
    return jsonify({'success': True, 'message': 'Logged out successfully'}), 200


@main.route('/api/grok-key', methods=['GET'])
@login_required
def grok_key_status():
    """Report whether a Grok key is currently bound to the user's session.

    The frontend uses this on startup (and after every Save/Clear) to update
    the cached ``hasValidGrokApiKey`` flag that drives the menu button state
    and the API-key-required modal. The actual key value is never returned.
    """
    user_id = session.get('user_id')
    present = bool(user_id is not None and _grok_key_store.has(user_id))
    # In dev, a GROK_API_KEY pulled from .env satisfies the gate too, so
    # report present so the frontend doesn't surface the API-key-required
    # modal. Production sets DEV_GROK_API_KEY to None so this is a no-op.
    if not present and current_app.config.get('DEV_GROK_API_KEY'):
        present = True
    return jsonify({'present': present}), 200


@main.route('/api/grok-key', methods=['POST'])
@login_required
@limit("20 per hour")
def grok_key_set():
    """Bind an xAI API key to the caller's session.

    The key arrives in the JSON body of this single endpoint, never on any
    other request. It is encrypted with a SECRET_KEY-derived Fernet token
    and held in process memory under the authenticated user_id; subsequent
    gated routes look it up via _extract_grok_api_key() without the key
    travelling on the wire again.
    """
    data = request.get_json(silent=True) or {}
    api_key = (data.get('api_key') or '').strip()
    if not _is_valid_grok_key(api_key):
        return jsonify({
            'success': False,
            'message': 'A valid xAI API key (starting with "xai-") is required.'
        }), 400
    user_id = session.get('user_id')
    if user_id is None:
        # login_required already enforces this in production; defensive guard
        # for ad-hoc apps that mount the blueprint without the gate.
        return jsonify({'success': False, 'message': 'Authentication required.'}), 401
    _grok_key_store.set(user_id, api_key)
    return jsonify({'success': True, 'present': True}), 200


@main.route('/api/grok-key', methods=['DELETE'])
@login_required
def grok_key_clear():
    """Drop the user's stored Grok key from the in-memory store."""
    user_id = session.get('user_id')
    if user_id is not None:
        _grok_key_store.clear(user_id)
    return jsonify({'success': True, 'present': False}), 200


# --- ElevenLabs key + TTS ---------------------------------------------------
# Same transport pattern as the Grok key: the value lives in an encrypted
# server-side store keyed by user_id and is never echoed back to the browser.
# TTS is opt-in, so these routes are not gated by ``grok_api_key_required``.
def _is_valid_elevenlabs_key(key):
    return bool(key) and len(key) >= 16


@main.route('/api/elevenlabs-key', methods=['GET'])
@login_required
def elevenlabs_key_status():
    user_id = session.get('user_id')
    present = bool(user_id is not None and _elevenlabs_key_store.has(user_id))
    if not present and current_app.config.get('DEV_ELEVENLABS_API_KEY'):
        present = True
    return jsonify({'present': present}), 200


@main.route('/api/elevenlabs-key', methods=['POST'])
@login_required
@limit("20 per hour")
def elevenlabs_key_set():
    data = request.get_json(silent=True) or {}
    api_key = (data.get('api_key') or '').strip()
    if not _is_valid_elevenlabs_key(api_key):
        return jsonify({
            'success': False,
            'message': 'A valid ElevenLabs API key is required.'
        }), 400
    user_id = session.get('user_id')
    if user_id is None:
        return jsonify({'success': False, 'message': 'Authentication required.'}), 401
    _elevenlabs_key_store.set(user_id, api_key)
    return jsonify({'success': True, 'present': True}), 200


@main.route('/api/elevenlabs-key', methods=['DELETE'])
@login_required
def elevenlabs_key_clear():
    user_id = session.get('user_id')
    if user_id is not None:
        _elevenlabs_key_store.clear(user_id)
    return jsonify({'success': True, 'present': False}), 200


def _tts_cache_dir():
    base = os.path.join(current_app.instance_path, 'tts_cache')
    try:
        os.makedirs(base, exist_ok=True)
    except OSError:
        pass
    return base


def _resolve_voice_id_for_speaker(db_session, seed_id, speaker):
    """Map a transcript entry's speaker to a Character.voice_id.

    Falls back to the narrator voice when the speaker is the narrator,
    unknown, or the matching Character has no voice_id assigned (e.g.
    the world was built before an ElevenLabs key was bound). Matching
    is case-insensitive and falls back to the first name so dialogue
    written as just "Lyra" still resolves to "Lyra Aldun".
    """
    if not speaker or speaker.strip().lower() in {'narrator', 'you', 'system'}:
        return elevenlabs_service.NARRATOR_VOICE_ID
    cleaned = speaker.strip()
    char = (
        db_session.query(Character)
        .filter(Character.seed_id == seed_id,
                func.lower(Character.name) == cleaned.lower())
        .first()
    )
    if char is None:
        first = cleaned.split()[0].lower()
        char = (
            db_session.query(Character)
            .filter(Character.seed_id == seed_id,
                    func.lower(Character.name).like(f'{first} %'))
            .first()
        )
    if char and char.voice_id:
        return char.voice_id
    return elevenlabs_service.NARRATOR_VOICE_ID


@main.route('/api/tts/<int:seed_id>/<int:entry_id>', methods=['GET'])
@login_required
@limit("120 per hour")
def tts_for_entry(seed_id, entry_id):
    """Return mp3 audio for a single transcript entry.

    Resolves the speaker on the entry to a Character voice id (or the
    narrator default), then delegates to the ElevenLabs service which
    transparently caches results on disk so repeat requests for the same
    line don't bill the user's quota a second time. Returns 404 when the
    entry doesn't belong to the seed and 503 when no audio could be
    produced (no key + cold cache, or upstream failure).
    """
    Session = current_app.config['SESSION_FACTORY']
    db_session = Session()
    try:
        if _seed_owned_by_caller(db_session, seed_id) is None:
            return jsonify({'success': False, 'message': 'Entry not found.'}), 404
        entry = (
            db_session.query(TranscriptEntry)
            .filter(TranscriptEntry.id == entry_id,
                    TranscriptEntry.seed_id == seed_id)
            .first()
        )
        if not entry or not entry.text:
            return jsonify({'success': False, 'message': 'Entry not found.'}), 404

        voice_id = _resolve_voice_id_for_speaker(db_session, seed_id, entry.speaker)
        api_key = _extract_elevenlabs_api_key()
        audio = elevenlabs_service.synthesize(
            api_key, voice_id, entry.text, cache_dir=_tts_cache_dir(),
        )
        if not audio:
            return jsonify({
                'success': False,
                'message': 'TTS unavailable (missing key or upstream error).'
            }), 503
        resp = Response(audio, mimetype='audio/mpeg')
        # Browser-side cache: entry text + voice are immutable so the
        # client can safely keep the rendering for the session lifetime.
        resp.headers['Cache-Control'] = 'private, max-age=86400'
        return resp
    finally:
        db_session.close()


@main.route('/auth/check', methods=['GET'])
def check_auth():
    if 'user_id' in session:
        return jsonify({'authenticated': True, 'username': session.get('username')}), 200
    return jsonify({'authenticated': False}), 200

@main.route('/analyze_stereotype', methods=['POST'])
@login_required
@grok_api_key_required
@limit("20 per hour")
def analyze_stereotype():
    """Analyze an uploaded image and generate a stereotypical character build using Grok Vision API"""
    try:
        data = request.get_json(silent=True) or {}
        image_data = data.get('image_data')
        # Header-only transport: the @grok_api_key_required decorator already
        # rejected the request if the header is missing or malformed, so by
        # this point _extract_grok_api_key() is guaranteed to return a key
        # that satisfies _is_valid_grok_key().
        grok_api_key = _extract_grok_api_key()

        if not image_data:
            return jsonify({'success': False, 'message': 'No image data provided'}), 400

        # Extract base64 image data (remove data:image/...;base64, prefix if present)
        if ',' in image_data:
            image_data = image_data.split(',')[1]

        prompt = STEREOTYPE_ANALYSIS

        # Prepare the request to Grok Vision API
        headers = {
            'Authorization': f'Bearer {grok_api_key}',
            'Content-Type': 'application/json'
        }

        # Grok Vision API expects messages with image content
        body = {
            "model": "grok-2-vision-1212",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": prompt
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_data}"
                            }
                        }
                    ]
                }
            ],
            "temperature": 0.5
        }

        # Call Grok Vision API
        response = requests.post(
            'https://api.x.ai/v1/chat/completions',
            json=body,
            headers=headers,
            timeout=30
        )

        if not response.ok:
            error_msg = response.json().get('error', {}).get('message', 'Unknown error')
            return jsonify({'success': False, 'message': f'Grok API error: {error_msg}'}), 400

        # Extract the response
        grok_response = response.json()
        content = grok_response['choices'][0]['message']['content']

        # Parse JSON from the response
        try:
            # Try to extract JSON from the response
            start_idx = content.find('{')
            end_idx = content.rfind('}') + 1

            if start_idx == -1 or end_idx == 0:
                return jsonify({'success': False, 'message': 'Could not parse JSON from response'}), 500

            json_str = content[start_idx:end_idx]
            build_data = json.loads(json_str)

            # Validate required fields
            required_fields = ['character_name', 'character_age', 'character_gender', 'story_inspiration']
            for field in required_fields:
                if field not in build_data:
                    build_data[field] = ''

            return jsonify({
                'success': True,
                'build': build_data,
                'raw_response': content
            }), 200

        except json.JSONDecodeError:
            current_app.logger.exception('analyze_stereotype JSON parse failed')
            return jsonify({
                'success': False,
                'message': 'Failed to parse model response.'
            }), 500

    except Exception:
        current_app.logger.exception('analyze_stereotype failed')
        return jsonify({'success': False, 'message': 'Server error.'}), 500