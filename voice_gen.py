"""ElevenLabs TTS wrapper — REST API, no SDK."""

import os
import uuid
import requests
import db

ELEVENLABS_API_KEY = os.environ.get('ELEVENLABS_API_KEY')
ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech"

# Voice type -> ElevenLabs voice ID mapping (populated at startup)
_VOICE_MAP = {}

# Fallback narrator voice (Rachel)
DEFAULT_NARRATOR_VOICE = "21m00Tcm4TlvDq8ikWAM"


def _get_headers():
    return {"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"}


def init_voice_map():
    """Fetch voices from ElevenLabs account and build type -> voice_id mapping."""
    global _VOICE_MAP
    if not ELEVENLABS_API_KEY:
        print("⚠️ ELEVENLABS_API_KEY not set — voice map empty")
        return

    try:
        resp = requests.get(
            "https://api.elevenlabs.io/v1/voices",
            headers={"xi-api-key": ELEVENLABS_API_KEY},
            timeout=10
        )
        resp.raise_for_status()
        voices = resp.json().get("voices", [])
        by_name = {v["name"].lower(): v["voice_id"] for v in voices}

        type_preferences = {
            "deep_male": ["adam", "daniel", "marcus", "clyde", "arnold"],
            "young_male": ["josh", "sam", "patrick", "harry", "liam"],
            "old_male": ["bill", "george", "thomas", "arthur", "james"],
            "deep_female": ["nicole", "domi", "rachel", "charlotte", "sarah"],
            "young_female": ["bella", "elli", "emily", "lily", "jessica"],
            "old_female": ["dorothy", "grace", "margaret", "glinda", "matilda"],
            "mysterious": ["freya", "gigi", "aria", "callum", "fin"],
            "villain": ["clyde", "arnold", "adam", "onyx", "drew"],
            "narrator": ["rachel", "adam", "daniel", "aria", "alloy"],
        }

        for voice_type, preferences in type_preferences.items():
            for name in preferences:
                if name in by_name:
                    _VOICE_MAP[voice_type] = by_name[name]
                    break
            if voice_type not in _VOICE_MAP and voices:
                _VOICE_MAP[voice_type] = voices[0]["voice_id"]

        print(f"✅ Voice map: {len(_VOICE_MAP)} types from {len(voices)} voices")
        for vtype, vid in _VOICE_MAP.items():
            name = next((v["name"] for v in voices if v["voice_id"] == vid), "?")
            print(f"   {vtype} -> {name} ({vid})")

    except Exception as e:
        print(f"❌ Voice map init failed: {e}")


def get_voice_id(voice_type: str, character_voice_id: str = None) -> str:
    """Resolve voice ID: character override > type map > default."""
    if character_voice_id:
        return character_voice_id
    return _VOICE_MAP.get(voice_type, DEFAULT_NARRATOR_VOICE)


def generate_speech(text: str, voice_id: str, game_id: str, label: str = "audio") -> str | None:
    """Generate TTS audio, upload to Supabase Storage. Returns public URL."""
    if not ELEVENLABS_API_KEY:
        return None

    try:
        resp = requests.post(
            f"{ELEVENLABS_TTS_URL}/{voice_id}",
            headers=_get_headers(),
            json={
                "text": text,
                "model_id": "eleven_turbo_v2_5",
                "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}
            },
            timeout=30
        )
        resp.raise_for_status()

        filename = f"{game_id}/{label}_{uuid.uuid4().hex[:8]}.mp3"
        client = db.get_client()
        client.storage.from_("gauntlet-media").upload(
            filename, resp.content,
            file_options={"content-type": "audio/mpeg"}
        )
        return client.storage.from_("gauntlet-media").get_public_url(filename)

    except Exception as e:
        print(f"❌ TTS failed ({label}): {e}")
        return None
