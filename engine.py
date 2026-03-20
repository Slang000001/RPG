"""Game engine — GPT integration, game state management, parallel media pipeline."""

import json
import os
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

import db
from image_gen import generate_image, generate_portrait
from voice_gen import generate_speech, resolve_voice_id, get_voice_list_for_prompt, NARRATOR_VOICE

OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')
GPT_MODEL = "gpt-5.2-chat-latest"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"

_executor = ThreadPoolExecutor(max_workers=12)

_PROMPTS_DIR = os.path.join(os.path.dirname(__file__), 'prompts')

# Precomputed turn cache: (game_id, turn_number, choice) -> claude result dict
_precomputed = {}


def _load_prompt(filename: str) -> str:
    with open(os.path.join(_PROMPTS_DIR, filename), 'r') as f:
        return f.read()


def _gpt_request(messages: list) -> str:
    """Send messages to OpenAI, return raw text response."""
    resp = requests.post(
        OPENAI_URL,
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "model": GPT_MODEL,
            "max_completion_tokens": 4096,
            "messages": messages,
            "response_format": {"type": "json_object"}
        },
        timeout=60
    )
    if not resp.ok:
        print(f"❌ GPT API error {resp.status_code}: {resp.text[:500]}")
        resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


def _call_llm(prompt: str) -> dict:
    """Call GPT and return parsed JSON. Uses json_object mode for guaranteed valid JSON."""
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY not set")

    messages = [
        {"role": "system", "content": "You are a game master. Always respond with valid JSON only."},
        {"role": "user", "content": prompt}
    ]
    text = _gpt_request(messages)
    result = json.loads(text)
    # Log the image prompt for debugging
    if result.get("image_prompt"):
        print(f"🖼️ IMAGE PROMPT: {result['image_prompt'][:500]}")
    return result


def _get_characters_for_game(game_id: str) -> list[dict]:
    client = db.get_client()
    result = client.table("gauntlet_characters").select("*").eq("game_id", game_id).execute()
    return result.data or []


def _find_character(char_name: str, characters: list) -> dict:
    """Find a character by name with fuzzy matching — exact first, then substring."""
    # Exact match
    for c in characters:
        if c["name"] == char_name:
            return c
    # Substring match (handles "Espe" matching "Esperanza Vega")
    name_lower = char_name.lower()
    for c in characters:
        if name_lower in c["name"].lower() or c["name"].lower() in name_lower:
            return c
    print(f"⚠️ No character match for '{char_name}', using narrator voice")
    return {}


def _get_face_urls(characters: list, game_state: dict = None) -> list[str]:
    """Collect portrait URLs from characters present in the scene."""
    urls = []
    # Player portrait if available
    if game_state and game_state.get("player_portrait_url"):
        urls.append(game_state["player_portrait_url"])
    # NPC portraits
    for c in characters:
        portrait = c.get("voice_id_portrait") or c.get("portrait_url")
        if portrait:
            urls.append(portrait)
    return urls


def _generate_turn_media(game_id: str, narration: str, dialogue: list, characters: list, image_prompt: str, game_state: dict = None) -> dict:
    """Generate image in parallel with sequential TTS to avoid socket exhaustion."""

    # Image generation runs in parallel with audio
    face_urls = _get_face_urls(characters, game_state)
    image_future = None
    if image_prompt:
        image_future = _executor.submit(generate_image, image_prompt, game_id, face_urls or None)

    # TTS runs sequentially to avoid resource exhaustion
    narration_audio_url = None
    if narration:
        narration_audio_url = generate_speech(narration, NARRATOR_VOICE, game_id, "narration")

    updated_dialogue = []
    for line in dialogue:
        char_name = line.get("character_name", "")
        char = _find_character(char_name, characters)
        # Use pre-resolved voice_id from DB — guaranteed consistent per character
        voice_id = char.get("voice_id") or resolve_voice_id(char.get("voice_type", ""))
        audio_url = generate_speech(line["line"], voice_id, game_id, f"char_{char_name}")
        updated_dialogue.append({**line, "audio_url": audio_url})

    # Collect image result
    image_url = None
    if image_future:
        try:
            image_url = image_future.result(timeout=45)
        except Exception as e:
            print(f"⚠️ Image gen failed: {e}")

    return {
        "image_url": image_url,
        "narration_audio_url": narration_audio_url,
        "dialogue": updated_dialogue
    }


def _generate_and_update_media(turn_id: str, game_id: str, narration: str,
                                dialogue: list, characters: list, image_prompt: str, game_state: dict = None):
    """Background task: generate media and update the turn row in DB."""
    try:
        media = _generate_turn_media(game_id, narration, dialogue, characters, image_prompt, game_state)
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

def create_game(user_id: str, setting: str, tone: str, genre: str, player: dict, character_descs: list[dict]) -> dict:
    """Create a new game: world seed via Claude, parallel media, persist to DB."""
    template = _load_prompt("world_seed.md")
    characters_text = "\n".join(
        f"- **{c['name']}**: {c.get('description', 'No description')}"
        for c in character_descs
    )
    player_text = f"**{player['name']}**: {player['appearance']}"
    prompt = template.replace("{{setting}}", setting) \
                      .replace("{{tone}}", tone) \
                      .replace("{{genre}}", genre) \
                      .replace("{{player_character}}", player_text) \
                      .replace("{{characters}}", characters_text) \
                      .replace("{{voice_list}}", get_voice_list_for_prompt())

    seed = _call_llm(prompt)
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

    # Generate player portrait
    print(f"🎨 Generating player portrait for {player['name']}...")
    player_portrait_url = generate_portrait(player["appearance"], game_id)

    # Create characters + generate portraits
    characters = []
    for char_data in seed.get("characters", []):
        voice_name = char_data.get("voice_name", "")
        appearance = char_data.get("appearance", char_data.get("description", ""))
        print(f"🎨 Generating portrait for {char_data['name']}...")
        portrait_url = generate_portrait(appearance, game_id)
        char_result = client.table("gauntlet_characters").insert({
            "game_id": game_id,
            "name": char_data["name"],
            "description": appearance,
            "personality": char_data.get("personality", ""),
            "voice_type": voice_name,
            "voice_id": resolve_voice_id(voice_name),
            "portrait_url": portrait_url,
        }).execute()
        characters.append(char_result.data[0])

    # Initial game state
    opening = seed["opening_scene"]
    initial_state = seed.get("initial_game_state", {})
    initial_state["turn_count"] = 0
    initial_state["world_summary"] = seed.get("world_summary", "")
    initial_state["tone"] = tone
    initial_state["genre"] = genre
    initial_state["player_name"] = player["name"]
    initial_state["player_appearance"] = player["appearance"]
    initial_state["player_portrait_url"] = player_portrait_url
    # Store short image tags for each character (used in image prompts)
    initial_state["image_tags"] = {
        player["name"]: f"player character, {player['appearance'][:60]}"
    }
    for char_data in seed.get("characters", []):
        tag = char_data.get("image_tag", "")
        if tag:
            initial_state["image_tags"][char_data["name"]] = tag

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
                     characters, opening.get("image_prompt", ""), initial_state)

    # Precompute all 3 choices for turn 0 in background
    opening_choices = opening.get("choices", [])
    if opening_choices:
        _executor.submit(_precompute_choices, game_id, 0,
                         game, initial_state, characters, opening_choices)

    return {"game": game, "characters": characters, "turn": turn}


def _build_turn_prompt(game: dict, game_state: dict, characters: list, choice_text: str) -> str:
    """Build a turn prompt from game context and a choice."""
    template = _load_prompt("turn.md")
    # Player character appearance (locked from game creation)
    player_name = game_state.get("player_name", "the player")
    player_appearance = game_state.get("player_appearance", "")
    player_line = f"- **{player_name} (the player, 'you')**: Appearance: {player_appearance}" if player_appearance else ""
    npc_lines = "\n".join(
        f"- **{c['name']}**: Appearance: {c.get('description', '')} | Personality: {c.get('personality', '')}"
        for c in characters
    )
    characters_text = f"{player_line}\n{npc_lines}" if player_line else npc_lines
    # Build image tags block
    image_tags = game_state.get("image_tags", {})
    image_tags_text = "\n".join(f"- {name}: {tag}" for name, tag in image_tags.items())
    print(f"📋 CHARACTERS IN PROMPT ({len(characters)+1 if player_line else len(characters)})")
    print(f"🏷️ IMAGE TAGS:\n{image_tags_text}")
    return template.replace("{{world_summary}}", game.get("world_summary", "")) \
                   .replace("{{game_state}}", json.dumps(game_state, indent=2)) \
                   .replace("{{characters}}", characters_text) \
                   .replace("{{image_tags}}", image_tags_text) \
                   .replace("{{player_choice}}", choice_text) \
                   .replace("{{tone}}", game.get("tone", "")) \
                   .replace("{{genre}}", game.get("genre", ""))


def _precompute_single_choice(game_id: str, turn_number: int, choice_num: int,
                               game: dict, game_state: dict, characters: list, choice_text: str):
    """Precompute a single choice: Claude call + full media generation."""
    cache_key = (game_id, turn_number, choice_num)
    try:
        prompt = _build_turn_prompt(game, game_state, characters, choice_text)
        result = _call_llm(prompt)

        # Generate full media for this branch
        media = _generate_turn_media(
            game_id, result.get("narration", ""),
            result.get("dialogue", []), characters,
            result.get("image_prompt", ""), game_state
        )

        _precomputed[cache_key] = {"result": result, "media": media}
        print(f"✅ Precomputed choice {choice_num} (text+media) for turn {turn_number} of game {game_id[:8]}")
    except Exception as e:
        print(f"⚠️ Precompute failed for choice {choice_num}: {e}")


def _precompute_choices(game_id: str, turn_number: int, game: dict,
                        game_state: dict, characters: list, choices: list):
    """Background: precompute Claude responses + full media sequentially to avoid rate limits."""
    for i, choice_text in enumerate(choices):
        _precompute_single_choice(game_id, turn_number, i + 1,
                                  game, game_state, characters, choice_text)

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
    cached = _precomputed.pop(cache_key, None)

    if cached:
        result = cached["result"]
        media = cached["media"]
        print(f"⚡ Cache hit (text+media) for choice {player_choice} on turn {latest_turn['turn_number']}")
    else:
        # Cache miss — generate on the fly
        choice_text = choices[player_choice - 1] if 0 < player_choice <= len(choices) else "Unknown"
        prompt = _build_turn_prompt(game, game_state, characters, choice_text)
        result = _call_llm(prompt)
        media = None
        print(f"🐌 Cache miss for choice {player_choice} on turn {latest_turn['turn_number']}")

    new_state = result.get("updated_game_state", game_state)
    new_turn_number = latest_turn["turn_number"] + 1
    new_state["turn_count"] = new_turn_number
    new_state["world_summary"] = game.get("world_summary", "")
    new_state["tone"] = game.get("tone", "")
    new_state["genre"] = game.get("genre", "")

    if media:
        # Full precompute — save turn WITH media
        turn_result = client.table("gauntlet_turns").insert({
            "game_id": game_id,
            "turn_number": new_turn_number,
            "game_state": new_state,
            "narration_text": result.get("narration", ""),
            "narration_audio_url": media["narration_audio_url"],
            "dialogue": media["dialogue"],
            "image_url": media["image_url"],
            "image_prompt": result.get("image_prompt", ""),
            "choices": result.get("choices", []),
        }).execute()
    else:
        # Cache miss — save text only, generate media in background
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
        _executor.submit(_generate_and_update_media, turn_result.data[0]["id"], game_id,
                         result.get("narration", ""), result.get("dialogue", []),
                         characters, result.get("image_prompt", ""), new_state)

    turn = turn_result.data[0]

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
