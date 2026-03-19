"""Gemini Imagen wrapper — REST API, no SDK."""

import os
import base64
import time
import requests
import uuid
import db

GOOGLE_AI_API_KEY = os.environ.get('GOOGLE_AI_API_KEY')
IMAGEN_URL = "https://generativelanguage.googleapis.com/v1beta/models/imagen-4.0-generate-001:predict"


IMAGE_STYLE_PREFIX = "Photorealistic, dramatic cinematic lighting, high detail. "


def generate_image(prompt: str, game_id: str) -> str | None:
    """Generate image via Gemini Imagen, upload to Supabase Storage. Returns public URL."""
    if not GOOGLE_AI_API_KEY:
        print("⚠️ GOOGLE_AI_API_KEY not set — skipping image generation")
        return None

    # Prepend photorealistic style
    styled_prompt = IMAGE_STYLE_PREFIX + prompt

    for attempt in range(3):
        try:
            resp = requests.post(
                IMAGEN_URL,
                headers={"x-goog-api-key": GOOGLE_AI_API_KEY},
                json={
                    "instances": [{"prompt": styled_prompt}],
                    "parameters": {
                        "sampleCount": 1,
                        "aspectRatio": "16:9",
                        "safetyFilterLevel": "BLOCK_ONLY_HIGH"
                    }
                },
                timeout=30
            )

            if resp.status_code == 429:
                wait = (attempt + 1) * 5  # 5s, 10s, 15s
                print(f"⚠️ Imagen rate limited, retrying in {wait}s (attempt {attempt + 1}/3)")
                time.sleep(wait)
                continue

            resp.raise_for_status()
            data = resp.json()

            predictions = data.get("predictions", [])
            if not predictions:
                print("⚠️ Imagen returned no predictions")
                return None

            image_bytes = base64.b64decode(predictions[0]["bytesBase64Encoded"])
            filename = f"{game_id}/{uuid.uuid4().hex}.png"

            client = db.get_client()
            client.storage.from_("gauntlet-media").upload(
                filename, image_bytes,
                file_options={"content-type": "image/png"}
            )
            return client.storage.from_("gauntlet-media").get_public_url(filename)

        except Exception as e:
            print(f"❌ Image generation failed: {e}")
            return None

    print("❌ Image generation failed: rate limited after 3 retries")
    return None
