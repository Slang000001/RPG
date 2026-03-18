"""The Gauntlet — Voice-Driven AI RPG for Adobe Tiger Team."""

from flask import Flask, request, jsonify, send_file
import os

from auth import get_authenticated_user, AuthError
from engine import create_game, process_turn, load_game, list_games, get_turn_history
from voice_gen import init_voice_map

app = Flask(__name__, static_folder='static')


@app.errorhandler(AuthError)
def handle_auth_error(e):
    return e.to_response()


# ==================== Config ====================

SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_ANON_KEY = os.environ.get('SUPABASE_ANON_KEY')


@app.route('/api/config.js')
def api_config_js():
    """Public Supabase config for frontend auth."""
    js = f"const SUPABASE_URL = '{SUPABASE_URL}';\nconst SUPABASE_ANON_KEY = '{SUPABASE_ANON_KEY}';\n"
    return js, 200, {'Content-Type': 'application/javascript'}


# ==================== Page Routes ====================

@app.route('/')
def index():
    return send_file('static/index.html')


@app.route('/login')
def login_page():
    return send_file('static/login.html')


# ==================== API: Games ====================

@app.route('/api/games', methods=['POST'])
def api_create_game():
    """Create a new game — Claude generates world seed, parallel media generation."""
    user_id, _email = get_authenticated_user()
    data = request.get_json()

    if not data:
        return jsonify({"error": "Request body required"}), 400

    setting = data.get("setting", "").strip()
    tone = data.get("tone", "").strip()
    genre = data.get("genre", "").strip()
    characters = data.get("characters", [])

    if not setting or not tone or not genre:
        return jsonify({"error": "setting, tone, and genre are required"}), 400
    if not characters or len(characters) < 1:
        return jsonify({"error": "At least one character is required"}), 400
    if len(characters) > 3:
        return jsonify({"error": "Maximum 3 characters"}), 400

    try:
        result = create_game(user_id, setting, tone, genre, characters)
        return jsonify({"success": True, **result}), 201
    except Exception as e:
        print(f"❌ Game creation failed: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/games', methods=['GET'])
def api_list_games():
    """List user's saved games."""
    user_id, _email = get_authenticated_user()
    games = list_games(user_id)
    return jsonify({"success": True, "games": games})


@app.route('/api/games/<game_id>', methods=['GET'])
def api_get_game(game_id):
    """Load a game — returns latest turn state + content."""
    user_id, _email = get_authenticated_user()
    try:
        result = load_game(game_id)
        if result["game"]["user_id"] != user_id:
            return jsonify({"error": "Not found"}), 404
        return jsonify({"success": True, **result})
    except Exception as e:
        print(f"❌ Game load failed: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/games/<game_id>/turns', methods=['POST'])
def api_submit_turn(game_id):
    """Submit a choice (1-3) — Claude processes turn, parallel media gen."""
    user_id, _email = get_authenticated_user()
    data = request.get_json()

    if not data:
        return jsonify({"error": "Request body required"}), 400

    choice = data.get("choice")
    if choice not in (1, 2, 3):
        return jsonify({"error": "choice must be 1, 2, or 3"}), 400

    try:
        game_data = load_game(game_id)
        if game_data["game"]["user_id"] != user_id:
            return jsonify({"error": "Not found"}), 404
        if game_data["game"]["status"] != "active":
            return jsonify({"error": "Game is not active"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    try:
        result = process_turn(game_id, choice)
        return jsonify({"success": True, **result}), 201
    except Exception as e:
        print(f"❌ Turn processing failed: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/games/<game_id>/turns', methods=['GET'])
def api_get_turns(game_id):
    """Turn history for scrollback."""
    user_id, _email = get_authenticated_user()
    try:
        game_data = load_game(game_id)
        if game_data["game"]["user_id"] != user_id:
            return jsonify({"error": "Not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    turns = get_turn_history(game_id)
    return jsonify({"success": True, "turns": turns})


# ==================== Startup ====================

print("🎮 Initializing The Gauntlet...")
init_voice_map()
print("🎮 The Gauntlet is ready.")

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=os.environ.get('DEBUG', 'false').lower() == 'true')
