"""ElevenLabs TTS wrapper — REST API, no SDK."""

import os
import uuid
import requests
import db

ELEVENLABS_API_KEY = os.environ.get('ELEVENLABS_API_KEY')
ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech"

# Voice type -> ElevenLabs voice ID mapping (populated at startup)
_VOICE_MAP = {}

# Hardcoded voice type -> voice ID mapping based on Scott's ElevenLabs library.
# These are curated picks from 35 available voices.
_STATIC_VOICE_MAP = {
    "deep_male":    "pNInz6obpgDQGcFmaJgB",  # Adam - Dominant, Firm
    "young_male":   "SOYHLrjzK2X1ezoPC6cr",  # Harry - Fierce Warrior
    "old_male":     "pqHfZKP75CvOlQylNhV4",  # Bill - Wise, Mature, Balanced
    "deep_female":  "EXAVITQu4vr4xnSDxMaL",  # Sarah - Mature, Reassuring
    "young_female": "cgSgspJ2msm6clMCkdW9",  # Jessica - Playful, Bright, Warm
    "old_female":   "XrExE9yKIg1WjnnlVkGX",  # Matilda - Knowledgable, Professional
    "mysterious":   "N2lVS1w4EtoT3dr4eOWO",  # Callum - Husky Trickster
    "villain":      "nPczCjzI2devNBz1zQrb",  # Brian - Deep, Resonant and Comforting (menacing when low)
    "narrator":     "goT3UYdM9bhm0n2lmKQx",  # Edward - British, Dark, Seductive
}

DEFAULT_NARRATOR_VOICE = "goT3UYdM9bhm0n2lmKQx"  # Edward


def _get_headers():
    return {"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"}


def init_voice_map():
    """Initialize voice map from static config. Validates voices still exist in account."""
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
        valid_ids = {v["voice_id"] for v in voices}
        id_to_name = {v["voice_id"]: v["name"] for v in voices}

        # Use static map, validating each voice still exists
        for voice_type, voice_id in _STATIC_VOICE_MAP.items():
            if voice_id in valid_ids:
                _VOICE_MAP[voice_type] = voice_id
            elif voices:
                _VOICE_MAP[voice_type] = voices[0]["voice_id"]

        print(f"✅ Voice map: {len(_VOICE_MAP)} types from {len(voices)} voices")
        for vtype, vid in _VOICE_MAP.items():
            print(f"   {vtype} -> {id_to_name.get(vid, '?')} ({vid})")

    except Exception as e:
        print(f"❌ Voice map init failed: {e}")
        # Fall back to static map without validation
        _VOICE_MAP.update(_STATIC_VOICE_MAP)


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
