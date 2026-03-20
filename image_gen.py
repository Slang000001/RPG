"""DALL-E 3 image generation — OpenAI API."""

import os
import time
import requests
import uuid
import db

OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')
DALLE_URL = "https://api.openai.com/v1/images/generations"

IMAGE_STYLE_PREFIX = "Photorealistic, dramatic cinematic lighting, high detail. "


def generate_image(prompt: str, game_id: str) -> str | None:
    """Generate image via DALL-E 3, upload to Supabase Storage. Returns public URL."""
    if not OPENAI_API_KEY:
        print("⚠️ OPENAI_API_KEY not set — skipping image generation")
        return None

    styled_prompt = IMAGE_STYLE_PREFIX + prompt
    # DALL-E 3 has a 4000 char prompt limit
    if len(styled_prompt) > 3900:
        styled_prompt = styled_prompt[:3900]

    for attempt in range(3):
        try:
            resp = requests.post(
                DALLE_URL,
                headers={
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "dall-e-3",
                    "prompt": styled_prompt,
                    "n": 1,
                    "size": "1792x1024",
                    "quality": "standard"
                },
                timeout=45
            )

            if resp.status_code == 429:
                wait = (attempt + 1) * 5
                print(f"⚠️ DALL-E rate limited, retrying in {wait}s (attempt {attempt + 1}/3)")
                time.sleep(wait)
                continue

            if not resp.ok:
                print(f"❌ DALL-E error {resp.status_code}: {resp.text[:300]}")
                resp.raise_for_status()
            data = resp.json()

            image_url = data["data"][0]["url"]

            # Download the image from OpenAI's temporary URL
            img_resp = requests.get(image_url, timeout=30)
            img_resp.raise_for_status()

            filename = f"{game_id}/{uuid.uuid4().hex}.png"
            client = db.get_client()
            client.storage.from_("gauntlet-media").upload(
                filename, img_resp.content,
                file_options={"content-type": "image/png"}
            )
            return client.storage.from_("gauntlet-media").get_public_url(filename)

        except Exception as e:
            if attempt < 2 and "rate" in str(e).lower():
                time.sleep((attempt + 1) * 5)
                continue
            print(f"❌ Image generation failed: {e}")
            return None

    print("❌ Image generation failed: rate limited after 3 retries")
    return None
