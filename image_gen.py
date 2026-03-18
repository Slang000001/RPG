"""Gemini Imagen wrapper — REST API, no SDK."""

import os
import base64
import requests
import uuid
import db

GOOGLE_AI_API_KEY = os.environ.get('GOOGLE_AI_API_KEY')
IMAGEN_URL = "https://generativelanguage.googleapis.com/v1beta/models/imagen-3.0-generate-002:predict"


def generate_image(prompt: str, game_id: str) -> str | None:
    """Generate image via Gemini Imagen, upload to Supabase Storage. Returns public URL."""
    if not GOOGLE_AI_API_KEY:
        print("⚠️ GOOGLE_AI_API_KEY not set — skipping image generation")
        return None

    try:
        resp = requests.post(
            f"{IMAGEN_URL}?key={GOOGLE_AI_API_KEY}",
            json={
                "instances": [{"prompt": prompt}],
                "parameters": {
                    "sampleCount": 1,
                    "aspectRatio": "16:9",
                    "safetyFilterLevel": "BLOCK_ONLY_HIGH"
                }
            },
            timeout=30
        )
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
