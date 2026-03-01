import datetime
import requests
import json
import base64
import queue
import threading
from werkzeug.security import generate_password_hash, check_password_hash

from flask import Blueprint, jsonify, render_template, current_app, request, send_from_directory, session, Response, stream_with_context
from sqlalchemy.exc import IntegrityError

from app.orm import Seed, User, Settings
from app.world_building.world_building import WorldBuilder
from app.prompt_templates import STEREOTYPE_ANALYSIS

main = Blueprint('main', __name__)
world_builder = None

# Store progress queues for each session
progress_queues = {}

@main.route('/', methods=['GET', 'POST'])
def index():
    return render_template('index.html')

@main.route('/templates/<path:filename>')
def serve_template(filename):
    return send_from_directory('templates', filename)

@main.route('/get_config')
def get_config():
    serializable_config = make_serializable(current_app.config)
    return jsonify(serializable_config)

@main.route('/create_seed', methods=['POST'])
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

@main.route('/api/settings', methods=['GET'])
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
            classes = current_app.config.get('classes', [])

            settings_record = Settings(
                min_grok=current_app.config.get('min_grok', 'grok-2-1212'),
                max_grok=current_app.config.get('max_grok', 'grok-2-1212'),
                emotional_attributes=json.dumps(emotional_attrs),
                classes=json.dumps(classes)
            )
            session.add(settings_record)
            session.commit()

        # Parse JSON fields
        emotional_attrs = json.loads(settings_record.emotional_attributes) if settings_record.emotional_attributes else {}
        classes = json.loads(settings_record.classes) if settings_record.classes else []

        settings = {
            'min_grok': settings_record.min_grok,
            'max_grok': settings_record.max_grok,
            'emotional_attributes': emotional_attrs,
            'classes': classes
        }

        session.close()
        return jsonify(settings)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@main.route('/api/settings/save', methods=['POST'])
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
        settings_record.min_grok = data.get('min_grok', 'grok-2-1212')
        settings_record.max_grok = data.get('max_grok', 'grok-2-1212')
        settings_record.emotional_attributes = json.dumps(data.get('emotional_attributes', {}))
        settings_record.classes = json.dumps(data.get('classes', []))
        settings_record.updated_at = datetime.datetime.now()

        session.commit()

        # Update app config
        current_app.config['min_grok'] = settings_record.min_grok
        current_app.config['max_grok'] = settings_record.max_grok
        current_app.config['emotional_attributes'] = json.loads(settings_record.emotional_attributes)
        current_app.config['classes'] = json.loads(settings_record.classes)

        session.close()
        return jsonify({'status': 'success', 'message': 'Settings saved successfully'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@main.route('/test-grok-key', methods=['POST'])
def test_grok_key():
    api_key = request.json['api_key']
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json'
    }
    body = {
        "model": "grok-2-1212",
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

    if not username or not password:
        return jsonify({'success': False, 'message': 'Username and password are required'}), 400

    Session = current_app.config['SESSION_FACTORY']
    db_session = Session()

    try:
        user = db_session.query(User).filter(User.username == username).first()

        if not user or not check_password_hash(user.password_hash, password):
            return jsonify({'success': False, 'message': 'Invalid username or password'}), 401

        # Set session
        session['user_id'] = user.id
        session['username'] = user.username

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

        # Get available classes from config
        classes = current_app.config.get('classes', [])
        classes_str = ', '.join(classes)

        # Format the prompt with available classes
        prompt = STEREOTYPE_ANALYSIS.format(classes=classes_str)

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
            required_fields = ['character_name', 'character_age', 'character_gender', 'character_class', 'story_inspiration']
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

def make_serializable(config):
    serializable_config = {}
    for key, value in config.items():
        if isinstance(value, (str, int, float, bool, list, dict, type(None))):
            serializable_config[key] = value
        elif isinstance(value, datetime.timedelta):
            serializable_config[key] = str(value)
        else:
            serializable_config[key] = str(value)
    return serializable_config