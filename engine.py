"""Game engine — Claude integration, game state management, parallel media pipeline."""

import json
import os
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

import db
from image_gen import generate_image
from voice_gen import generate_speech, get_voice_id

ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY')
CLAUDE_MODEL = "claude-haiku-4-5-20251001"
CLAUDE_URL = "https://api.anthropic.com/v1/messages"

_executor = ThreadPoolExecutor(max_workers=10)

_PROMPTS_DIR = os.path.join(os.path.dirname(__file__), 'prompts')

# Precomputed turn cache: (game_id, turn_number, choice) -> claude result dict
_precomputed = {}


def _load_prompt(filename: str) -> str:
    with open(os.path.join(_PROMPTS_DIR, filename), 'r') as f:
        return f.read()


def _call_claude(prompt: str) -> dict:
    """Call Claude Sonnet and return parsed JSON."""
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    resp = requests.post(
        CLAUDE_URL,
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        },
        json={
            "model": CLAUDE_MODEL,
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": prompt}]
        },
        timeout=60
    )
    resp.raise_for_status()
    data = resp.json()

    text = data["content"][0]["text"].strip()

    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.split('\n')
        # Remove first line (```json) and last line (```)
        end = len(lines) - 1
        while end > 0 and lines[end].strip() != '```':
            end -= 1
        text = '\n'.join(lines[1:end]).strip()

    return json.loads(text)


def _get_characters_for_game(game_id: str) -> list[dict]:
    client = db.get_client()
    result = client.table("gauntlet_characters").select("*").eq("game_id", game_id).execute()
    return result.data or []


def _generate_turn_media(game_id: str, narration: str, dialogue: list, characters: list, image_prompt: str) -> dict:
    """Fire image + all audio generation in parallel. Returns media URLs."""
    char_lookup = {c["name"]: c for c in characters}
    futures = {}

    if image_prompt:
        futures["image"] = _executor.submit(generate_image, image_prompt, game_id)

    if narration:
        narrator_voice = get_voice_id("narrator")
        futures["narration"] = _executor.submit(
            generate_speech, narration, narrator_voice, game_id, "narration"
        )

    for i, line in enumerate(dialogue):
        char_name = line.get("character_name", "")
        char = char_lookup.get(char_name, {})
        voice_id = get_voice_id(
            char.get("voice_type", "narrator"),
            char.get("voice_id")
        )
        futures[f"dialogue_{i}"] = _executor.submit(
            generate_speech, line["line"], voice_id, game_id, f"char_{char_name}"
        )

    results = {}
    for key, future in futures.items():
        try:
            results[key] = future.result(timeout=45)
        except Exception as e:
            print(f"⚠️ Media gen failed ({key}): {e}")
            results[key] = None

    updated_dialogue = []
    for i, line in enumerate(dialogue):
        entry = {**line, "audio_url": results.get(f"dialogue_{i}")}
        updated_dialogue.append(entry)

    return {
        "image_url": results.get("image"),
        "narration_audio_url": results.get("narration"),
        "dialogue": updated_dialogue
    }


def _generate_and_update_media(turn_id: str, game_id: str, narration: str,
                                dialogue: list, characters: list, image_prompt: str):
    """Background task: generate media and update the turn row in DB."""
    try:
        media = _generate_turn_media(game_id, narration, dialogue, characters, image_prompt)
        client = db.get_client()
        client.table("gauntlet_turns").update({
            "image_url": media["image_url"],
            "narration_audio_url": media["narration_audio_url"],
            "dialogue": media["dialogue"],
        }).eq("id", turn_id).execute()
        print(f"✅ Media updated for turn {turn_id}")
    except Exception as e:
        print(f"❌ Background media gen failed for turn {turn_id}: {e}")


def get_turn_media(turn_id: str) -> dict:
    """Check if media has been generated for a turn."""
    client = db.get_client()
    result = client.table("gauntlet_turns").select(
        "id, image_url, narration_audio_url, dialogue"
    ).eq("id", turn_id).single().execute()
    turn = result.data
    # Check that ALL media is ready: image + narration audio + all dialogue audio
    has_image = bool(turn.get("image_url"))
    has_narration_audio = bool(turn.get("narration_audio_url"))
    dialogue = turn.get("dialogue") or []
    has_all_dialogue_audio = all(d.get("audio_url") for d in dialogue) if dialogue else True
    all_media_ready = has_image and has_narration_audio and has_all_dialogue_audio
    return {"has_media": all_media_ready, "turn": turn}


# ==================== Public API ====================

def create_game(user_id: str, setting: str, tone: str, genre: str, character_descs: list[dict]) -> dict:
    """Create a new game: world seed via Claude, parallel media, persist to DB."""
    template = _load_prompt("world_seed.md")
    characters_text = "\n".join(
        f"- **{c['name']}**: {c.get('description', 'No description')}"
        for c in character_descs
    )
    prompt = template.replace("{{setting}}", setting) \
                      .replace("{{tone}}", tone) \
                      .replace("{{genre}}", genre) \
                      .replace("{{characters}}", characters_text)

    seed = _call_claude(prompt)
    client = db.get_client()

    # Create game
    game_result = client.table("gauntlet_games").insert({
        "user_id": user_id,
        "title": seed.get("title", f"{genre} Adventure"),
        "setting": setting,
        "tone": tone,
        "genre": genre,
        "world_summary": seed.get("world_summary", ""),
        "status": "active"
    }).execute()
    game = game_result.data[0]
    game_id = game["id"]

    # Create characters
    characters = []
    for char_data in seed.get("characters", []):
        char_result = client.table("gauntlet_characters").insert({
            "game_id": game_id,
            "name": char_data["name"],
            "description": char_data.get("description", ""),
            "personality": char_data.get("personality", ""),
            "voice_type": char_data.get("voice_type", "narrator"),
        }).execute()
        characters.append(char_result.data[0])

    # Initial game state
    opening = seed["opening_scene"]
    initial_state = seed.get("initial_game_state", {})
    initial_state["turn_count"] = 0
    initial_state["world_summary"] = seed.get("world_summary", "")
    initial_state["tone"] = tone
    initial_state["genre"] = genre

    # Save turn 0 immediately with text (no media yet)
    dialogue_no_audio = [{"character_name": d.get("character_name", ""), "line": d.get("line", "")}
                         for d in opening.get("dialogue", [])]
    turn_result = client.table("gauntlet_turns").insert({
        "game_id": game_id,
        "turn_number": 0,
        "game_state": initial_state,
        "narration_text": opening["narration"],
        "dialogue": dialogue_no_audio,
        "image_prompt": opening.get("image_prompt", ""),
        "choices": opening.get("choices", []),
    }).execute()
    turn = turn_result.data[0]

    # Fire media generation in background
    _executor.submit(_generate_and_update_media, turn["id"], game_id,
                     opening["narration"], opening.get("dialogue", []),
                     characters, opening.get("image_prompt", ""))

    # Precompute all 3 choices for turn 0 in background
    opening_choices = opening.get("choices", [])
    if opening_choices:
        _executor.submit(_precompute_choices, game_id, 0,
                         game, initial_state, characters, opening_choices)

    return {"game": game, "characters": characters, "turn": turn}


def _build_turn_prompt(game: dict, game_state: dict, characters: list, choice_text: str) -> str:
    """Build a turn prompt from game context and a choice."""
    template = _load_prompt("turn.md")
    characters_text = "\n".join(
        f"- **{c['name']}**: {c.get('description', '')} | Personality: {c.get('personality', '')}"
        for c in characters
    )
    return template.replace("{{world_summary}}", game.get("world_summary", "")) \
                   .replace("{{game_state}}", json.dumps(game_state, indent=2)) \
                   .replace("{{characters}}", characters_text) \
                   .replace("{{player_choice}}", choice_text) \
                   .replace("{{tone}}", game.get("tone", "")) \
                   .replace("{{genre}}", game.get("genre", ""))


def _precompute_choices(game_id: str, turn_number: int, game: dict,
                        game_state: dict, characters: list, choices: list):
    """Background: precompute Claude responses for all 3 choices."""
    for i, choice_text in enumerate(choices):
        choice_num = i + 1
        cache_key = (game_id, turn_number, choice_num)
        try:
            prompt = _build_turn_prompt(game, game_state, characters, choice_text)
            result = _call_claude(prompt)
            _precomputed[cache_key] = result
            print(f"✅ Precomputed choice {choice_num} for turn {turn_number} of game {game_id[:8]}")
        except Exception as e:
            print(f"⚠️ Precompute failed for choice {choice_num}: {e}")

    # Clean old entries (keep only current turn's precomputes per game)
    stale = [k for k in _precomputed if k[0] == game_id and k[1] != turn_number]
    for k in stale:
        del _precomputed[k]


def process_turn(game_id: str, player_choice: int) -> dict:
    """Process player choice, generate next turn via Claude + parallel media."""
    client = db.get_client()

    game = client.table("gauntlet_games").select("*").eq("id", game_id).single().execute().data
    latest_turn = client.table("gauntlet_turns").select("*") \
        .eq("game_id", game_id).order("turn_number", desc=True).limit(1).execute()
    if not latest_turn.data:
        raise ValueError("No turns found")
    latest_turn = latest_turn.data[0]

    # Record choice on current turn
    client.table("gauntlet_turns").update({"player_choice": player_choice}).eq("id", latest_turn["id"]).execute()

    characters = _get_characters_for_game(game_id)
    game_state = latest_turn["game_state"]
    choices = latest_turn.get("choices", [])

    # Check precomputed cache first
    cache_key = (game_id, latest_turn["turn_number"], player_choice)
    if cache_key in _precomputed:
        result = _precomputed.pop(cache_key)
        print(f"⚡ Cache hit for choice {player_choice} on turn {latest_turn['turn_number']}")
    else:
        # Cache miss — generate on the fly
        choice_text = choices[player_choice - 1] if 0 < player_choice <= len(choices) else "Unknown"
        prompt = _build_turn_prompt(game, game_state, characters, choice_text)
        result = _call_claude(prompt)
        print(f"🐌 Cache miss for choice {player_choice} on turn {latest_turn['turn_number']}")

    new_state = result.get("updated_game_state", game_state)
    new_turn_number = latest_turn["turn_number"] + 1
    new_state["turn_count"] = new_turn_number
    new_state["world_summary"] = game.get("world_summary", "")
    new_state["tone"] = game.get("tone", "")
    new_state["genre"] = game.get("genre", "")

    # Save turn immediately with text (no media yet)
    dialogue_no_audio = [{"character_name": d.get("character_name", ""), "line": d.get("line", "")}
                         for d in result.get("dialogue", [])]
    turn_result = client.table("gauntlet_turns").insert({
        "game_id": game_id,
        "turn_number": new_turn_number,
        "game_state": new_state,
        "narration_text": result.get("narration", ""),
        "dialogue": dialogue_no_audio,
        "image_prompt": result.get("image_prompt", ""),
        "choices": result.get("choices", []),
    }).execute()
    turn = turn_result.data[0]

    # Fire media generation in background
    _executor.submit(_generate_and_update_media, turn["id"], game_id,
                     result.get("narration", ""), result.get("dialogue", []),
                     characters, result.get("image_prompt", ""))

    # Precompute all 3 choices for the NEW turn in background
    new_choices = result.get("choices", [])
    if new_choices:
        _executor.submit(_precompute_choices, game_id, new_turn_number,
                         game, new_state, characters, new_choices)

    return {"game": game, "characters": characters, "turn": turn}


def load_game(game_id: str) -> dict:
    client = db.get_client()
    game = client.table("gauntlet_games").select("*").eq("id", game_id).single().execute().data
    characters = _get_characters_for_game(game_id)
    turn_result = client.table("gauntlet_turns").select("*") \
        .eq("game_id", game_id).order("turn_number", desc=True).limit(1).execute()
    return {
        "game": game,
        "characters": characters,
        "turn": turn_result.data[0] if turn_result.data else None
    }


def list_games(user_id: str) -> list[dict]:
    client = db.get_client()
    result = client.table("gauntlet_games").select("*") \
        .eq("user_id", user_id).order("updated_at", desc=True).execute()
    return result.data or []


def get_turn_history(game_id: str) -> list[dict]:
    client = db.get_client()
    result = client.table("gauntlet_turns").select("*") \
        .eq("game_id", game_id).order("turn_number").execute()
    return result.data or []
