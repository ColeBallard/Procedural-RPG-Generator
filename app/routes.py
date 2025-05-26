import datetime
import requests

from flask import Blueprint, jsonify, render_template, current_app, request, send_from_directory
from sqlalchemy.exc import IntegrityError

from app.orm import Seed
from app.world_building.world_building import WorldBuilder

main = Blueprint('main', __name__)
world_builder = None

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
    openai_api_key = data.get('openai_api_key')
    
    # Set the OpenAI API key for this request
    current_app.openai.api_key = openai_api_key

    Session = current_app.config['SESSION_FACTORY']
    session = Session()

    try:
        world_builder = WorldBuilder(seed_data, seed_id, session, current_app.openai, current_app.config['min_gpt'])
        
        # Orchestrate the world-building process by calling the build_world method
        results = world_builder.build_world()
        
        # Optionally, you can check for errors or partial failures in 'results'
        return jsonify(results), 200
    except Exception as e:
        session.rollback()
        return jsonify({"message": "An error occurred during world building", "error": str(e)}), 500
    finally:
        session.close()

@main.route('/api/settings', methods=['GET'])
def get_settings():
    # Retrieve settings from database
    settings = {}  # This would be your actual call to fetch settings
    return jsonify(settings)

@main.route('/api/settings/save', methods=['POST'])
def save_settings():
    # Save settings to database
    return jsonify(status="success")

@main.route('/test-openai-key', methods=['POST'])
def test_openai_key():
    api_key = request.json['api_key']
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json'
    }
    body = {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello!"}
        ]
    }
    response = requests.post('https://api.openai.com/v1/chat/completions', json=body, headers=headers)
    if response.ok:
        return jsonify({'valid': True, 'message': 'API key is valid.'})
    else:
        return jsonify({'valid': False, 'message': 'API key is not valid.', 'error': response.json()}), 400

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