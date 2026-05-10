import datetime
import requests
import json
import base64
import queue
import threading
import uuid
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash

from flask import Blueprint, jsonify, render_template, current_app, request, send_from_directory, session, Response, stream_with_context
from sqlalchemy.exc import IntegrityError
from openai import OpenAI

from app.orm import (
    Seed, User, Settings, Character, Location, Event, Quest,
    CharacterItem, Item, CharacterSkill, Skill, CharacterStatus, Status,
    CharacterRelationship,
)
from app.services import transcript_service
from app.services.gpt_service import GPTService
from app.world_building.world_building import WorldBuilder
from app.prompt_templates import STEREOTYPE_ANALYSIS, WORLD_BUILDING

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
    # Header-first lookup keeps a single client-side setup point (see
    # ajaxSetup in main.js) for both GET and POST. Falls back to the JSON
    # body for existing POST routes that already inline the key.
    key = request.headers.get('X-Grok-API-Key')
    if key:
        return key.strip()
    if request.is_json:
        body = request.get_json(silent=True) or {}
        return (body.get('grok_api_key') or '').strip()
    return ''


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

@main.route('/', methods=['GET', 'POST'])
def index():
    return render_template('index.html')

@main.route('/templates/<path:filename>')
def serve_template(filename):
    return send_from_directory('templates', filename)

@main.route('/create_seed', methods=['POST'])
@login_required
def create_seed():
    Session = current_app.config['SESSION_FACTORY']
    session = Session()

    max_retries = 5
    attempts = 0

    while attempts < max_retries:
        try:
            new_seed = Seed()
            session.add(new_seed)
            session.commit()
            return jsonify({"message": "Seed created successfully", "status": "success", "seed_id": new_seed.id}), 201
        except IntegrityError:
            session.rollback()  # Rollback the session to a clean state
            attempts += 1
            if attempts == max_retries:
                return jsonify({"message": "Failed to create a seed after multiple attempts", "status": "failure"}), 500
        finally:
            session.close()

@main.route('/initialize_world_building', methods=['POST'])
@login_required
@grok_api_key_required
def initialize_world_building():
    data = request.json
    seed_id = data.get('seed_id')
    seed_data = data.get('seed_data')
    grok_api_key = data.get('grok_api_key')

    # Set the Grok API key for this request (using OpenAI SDK with xAI base URL)
    current_app.openai.api_key = grok_api_key

    Session = current_app.config['SESSION_FACTORY']
    db_session = Session()

    try:
        world_builder = WorldBuilder(seed_data, seed_id, db_session, current_app.openai, current_app.config['min_grok'])

        # Orchestrate the world-building process by calling the build_world method
        results = world_builder.build_world()

        # Optionally, you can check for errors or partial failures in 'results'
        return jsonify(results), 200
    except Exception as e:
        db_session.rollback()
        return jsonify({"message": "An error occurred during world building", "error": str(e)}), 500
    finally:
        db_session.close()

@main.route('/initialize_world_building_stream', methods=['POST'])
@login_required
@grok_api_key_required
def initialize_world_building_stream():
    data = request.json
    seed_id = data.get('seed_id')
    seed_data = data.get('seed_data')
    grok_api_key = data.get('grok_api_key')

    # Capture the real app object and config values up front so the background
    # thread does not depend on the request-bound current_app proxy.
    app = current_app._get_current_object()
    session_factory = app.config['SESSION_FACTORY']
    model = app.config['min_grok']

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
                    progress_callback=progress_callback
                )

                results = world_builder.build_world()

                # Persist the opening narration (if produced) as a separate
                # transcript entry so it renders with narration styling and
                # is replayed on refresh. WorldBuilder returns it in-band so
                # the orchestrator stays free of session-factory plumbing.
                intro_narration = results.pop('intro_narration', None) if isinstance(results, dict) else None
                if intro_narration:
                    transcript_service.add_entry(
                        session_factory, seed_id,
                        transcript_service.KIND_NARRATION, intro_narration,
                        speaker='Narrator',
                    )

                q.put({'type': 'complete', 'results': results})
            except Exception as e:
                db_session.rollback()
                transcript_service.add_entry(
                    session_factory, seed_id,
                    transcript_service.KIND_WORLD_BUILDING, str(e),
                    status='error',
                )
                q.put({'type': 'error', 'message': str(e)})
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
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@main.route('/api/settings/save', methods=['POST'])
@login_required
def save_settings():
    """Save settings to database"""
    try:
        data = request.json
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
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@main.route('/api/seeds', methods=['GET'])
@login_required
def list_seeds():
    """Return all saved seeds with their main character name and creation time."""
    Session = current_app.config['SESSION_FACTORY']
    db_session = Session()
    try:
        seeds = db_session.query(Seed).order_by(Seed.created_at.desc()).all()
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
    except Exception as e:
        return jsonify({'error': str(e)}), 500
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
        seed = db_session.query(Seed).filter(Seed.id == seed_id).first()
        if not seed:
            return jsonify({'error': 'Seed not found'}), 404

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
            for loc in db_session.query(Location).filter(Location.seed_id == seed_id).all()
        ]

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
            for ev in db_session.query(Event).filter(Event.seed_id == seed_id).all()
        ]

        main_character = (
            db_session.query(Character)
            .filter(Character.seed_id == seed_id, Character.main_character == True)
            .first()
        )

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

        return jsonify({
            'seed_id': seed_id,
            'current_turn': seed.current_turn,
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
        }), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500
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

    starting_location = _loc(locations[0])
    other_locations = [_loc(l) for l in locations[1:]]

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

    return {
        'seed_data': '',  # filled in by callers from request body when relevant
        'character': character_payload,
        'starting_location': starting_location,
        'other_locations': other_locations,
        'transcript': transcript_text,
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
def get_suggestions(seed_id):
    """Return short action suggestions for the player's current situation."""
    Session = current_app.config['SESSION_FACTORY']
    db_session = Session()
    try:
        context = _build_turn_context(db_session, seed_id)
        if context is None:
            return jsonify({'suggestions': []}), 200
        gpt_service = _make_gpt_service()
        suggestions = _generate_suggestions(gpt_service, context)
        return jsonify({'suggestions': suggestions}), 200
    except Exception as e:
        return jsonify({'suggestions': [], 'error': str(e)}), 200
    finally:
        db_session.close()


@main.route('/api/seed/<int:seed_id>/turn', methods=['POST'])
@login_required
@grok_api_key_required
def submit_turn(seed_id):
    """Accept a player action, persist it, and return the narrator's reply.

    Persists the player's input as a ``player_input`` transcript entry, asks
    the LLM for a short continuation as the narrator, persists that as a
    ``narration`` entry, and returns both alongside a fresh batch of
    suggestions for the next turn.
    """
    data = request.json or {}
    action = (data.get('action') or '').strip()
    seed_data = data.get('seed_data') or ''

    if not action:
        return jsonify({'success': False, 'message': 'Action is required.'}), 400

    Session = current_app.config['SESSION_FACTORY']
    session_factory = Session
    db_session = Session()

    try:
        seed = db_session.query(Seed).filter(Seed.id == seed_id).first()
        if not seed:
            return jsonify({'success': False, 'message': 'Seed not found.'}), 404

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
        narration_prompt = WORLD_BUILDING['CONTINUE_NARRATIVE'].format(**context)
        try:
            narration = gpt_service.get_response(narration_prompt, temperature=1.0)
            narration = (narration or '').strip()
        except Exception as e:
            return jsonify({'success': False, 'message': f'Narration failed: {e}'}), 502

        if narration:
            transcript_service.add_entry(
                session_factory, seed_id,
                transcript_service.KIND_NARRATION, narration,
                turn=turn, speaker='Narrator',
            )

        # Bump the turn counter only after a successful narration so failed
        # turns can be retried without skipping ahead.
        seed.current_turn = turn + 1
        seed.updated_at = datetime.datetime.now()
        db_session.commit()

        # Refresh suggestions against the now-updated transcript so the next
        # batch reflects what just happened. Failure here is non-fatal.
        refreshed_context = _build_turn_context(db_session, seed_id) or context
        refreshed_context['seed_data'] = seed_data
        suggestions = _generate_suggestions(gpt_service, refreshed_context)

        return jsonify({
            'success': True,
            'narration': narration,
            'suggestions': suggestions,
            'turn': seed.current_turn,
        }), 200
    except Exception as e:
        db_session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500
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


@main.route('/test-grok-key', methods=['POST'])
@login_required
def test_grok_key():
    api_key = request.json['api_key']
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json'
    }
    body = {
        "model": "grok-4-1-fast-non-reasoning",
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello!"}
        ]
    }
    response = requests.post('https://api.x.ai/v1/chat/completions', json=body, headers=headers)
    if response.ok:
        return jsonify({'valid': True, 'message': 'API key is valid.'})
    else:
        return jsonify({'valid': False, 'message': 'API key is not valid.', 'error': response.json()}), 400

@main.route('/auth/signup', methods=['POST'])
def signup():
    data = request.json
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
    except Exception as e:
        db_session.rollback()
        return jsonify({'success': False, 'message': f'Error creating account: {str(e)}'}), 500
    finally:
        db_session.close()

@main.route('/auth/login', methods=['POST'])
def login():
    data = request.json
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
    except Exception as e:
        return jsonify({'success': False, 'message': f'Error logging in: {str(e)}'}), 500
    finally:
        db_session.close()

@main.route('/auth/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'success': True, 'message': 'Logged out successfully'}), 200

@main.route('/auth/check', methods=['GET'])
def check_auth():
    if 'user_id' in session:
        return jsonify({'authenticated': True, 'username': session.get('username')}), 200
    return jsonify({'authenticated': False}), 200

@main.route('/analyze_stereotype', methods=['POST'])
@login_required
@grok_api_key_required
def analyze_stereotype():
    """Analyze an uploaded image and generate a stereotypical character build using Grok Vision API"""
    try:
        data = request.json
        image_data = data.get('image_data')
        grok_api_key = data.get('grok_api_key')

        if not image_data:
            return jsonify({'success': False, 'message': 'No image data provided'}), 400

        if not grok_api_key:
            return jsonify({'success': False, 'message': 'Grok API key is required'}), 400

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

        except json.JSONDecodeError as e:
            return jsonify({
                'success': False,
                'message': f'Failed to parse JSON: {str(e)}',
                'raw_response': content
            }), 500

    except Exception as e:
        return jsonify({'success': False, 'message': f'Server error: {str(e)}'}), 500