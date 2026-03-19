"""ElevenLabs TTS wrapper — REST API, no SDK.
Uses only Scott's custom 'My Voices' library. Edward is always narrator.
Claude picks voices for characters at runtime from the available list."""

import os
import uuid
import requests
import db

ELEVENLABS_API_KEY = os.environ.get('ELEVENLABS_API_KEY')
ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech"

# Scott's custom voices only — Claude picks from these by name at world seed time
MY_VOICES = {
    "edward":      "goT3UYdM9bhm0n2lmKQx",  # British, Dark, Seductive, Low (NARRATOR)
    "jerry_b":     "QzTKubutNn9TjrB7Xb2Q",  # Brash, Mischievous and Strong
    "blondie":     "ShB6BQqbEXZxWO5511Qq",  # Seductive and Soft-Spoken
    "ivanna":      "tQ4MEZFJOzsahSEEZtHK",  # Seductive & Intimate
    "the_elf":     "e79twtVS2278lVZZQiAD",  # Small expert on big matters
    "brad":        "f5HLTX707KIM4SzJYzSz",  # Welcoming & Casual
    "preston":     "xfMeiSCf21GHlOp9LjKk",  # Pro Bedtime Voice
    "hannah":      "M7ya1YbaeFaPXljg9BpK",  # Hannah Jayne
    "emily":       "p43fx6U8afP2xoq1Ai9f",  # Australian Female
}

# Voice name -> ID lookup for Claude's picks
_voice_name_to_id = {}

NARRATOR_VOICE = "goT3UYdM9bhm0n2lmKQx"  # Edward, always


def _get_headers():
    return {"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"}


def init_voice_map():
    """Build name lookup from My Voices."""
    global _voice_name_to_id
    _voice_name_to_id = {name: vid for name, vid in MY_VOICES.items()}
    print(f"✅ Voice library: {len(MY_VOICES)} custom voices")
    for name, vid in MY_VOICES.items():
        role = "(NARRATOR)" if name == "edward" else ""
        print(f"   {name} -> {vid} {role}")


def get_voice_list_for_prompt() -> str:
    """Return a formatted list of available voices for Claude to pick from."""
    descriptions = {
        "jerry_b": "Jerry B — male, brash, mischievous, strong New York Italian energy",
        "blondie": "Blondie — female, seductive, soft-spoken, young British",
        "ivanna": "Ivanna — female, seductive, intimate, luxurious American",
        "the_elf": "The Elf — neutral/quirky, small and cheerful, fast-talking expert",
        "brad": "Brad — male, welcoming, casual, friendly young American",
        "preston": "Preston — male, warm bedtime-story voice, bass-baritone, soothing",
        "hannah": "Hannah Jayne — female, natural, neutral Australian, conversational",
        "emily": "Emily — female, middle-aged Australian, clear and informative",
    }
    return "\n".join(f"- `{name}`: {desc}" for name, desc in descriptions.items())


def resolve_voice_id(voice_name: str) -> str:
    """Resolve a voice name (from Claude's pick) to an ElevenLabs voice ID."""
    if not voice_name:
        return NARRATOR_VOICE
    # Normalize: lowercase, strip whitespace
    normalized = voice_name.strip().lower().replace(" ", "_").replace("-", "_")
    # Try direct match
    if normalized in _voice_name_to_id:
        return _voice_name_to_id[normalized]
    # Try partial match
    for name, vid in _voice_name_to_id.items():
        if name in normalized or normalized in name:
            return vid
    print(f"⚠️ Unknown voice name '{voice_name}', falling back to narrator")
    return NARRATOR_VOICE


def generate_speech(text: str, voice_id: str, game_id: str, label: str = "audio") -> str | None:
    """Generate TTS audio, upload to Supabase Storage. Retries on rate limit."""
    if not ELEVENLABS_API_KEY:
        return None

    for attempt in range(3):
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

            if resp.status_code == 429:
                wait = (attempt + 1) * 3  # 3s, 6s, 9s
                print(f"⚠️ TTS rate limited ({label}), retrying in {wait}s (attempt {attempt + 1}/3)")
                import time
                time.sleep(wait)
                continue

            resp.raise_for_status()

            filename = f"{game_id}/{label}_{uuid.uuid4().hex[:8]}.mp3"
            client = db.get_client()
            client.storage.from_("gauntlet-media").upload(
                filename, resp.content,
                file_options={"content-type": "audio/mpeg"}
            )
            return client.storage.from_("gauntlet-media").get_public_url(filename)

        except Exception as e:
            if attempt < 2 and "Resource temporarily unavailable" in str(e):
                import time
                time.sleep((attempt + 1) * 3)
                continue
            print(f"❌ TTS failed ({label}): {e}")
            return None

    print(f"❌ TTS failed ({label}): rate limited after 3 retries")
    return None
