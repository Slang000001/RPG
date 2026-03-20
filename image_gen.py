"""Replicate SDXL image generation — no content filter, no rate limit drama."""

import os
import time
import requests
import uuid
import db

REPLICATE_API_TOKEN = os.environ.get('REPLICATE_API_TOKEN')
REPLICATE_URL = "https://api.replicate.com/v1/predictions"

IMAGE_STYLE_PREFIX = "Photorealistic, dramatic cinematic lighting, high detail. "


def generate_image(prompt: str, game_id: str) -> str | None:
    """Generate image via Replicate SDXL, upload to Supabase Storage. Returns public URL."""
    if not REPLICATE_API_TOKEN:
        print("⚠️ REPLICATE_API_TOKEN not set — skipping image generation")
        return None

    styled_prompt = IMAGE_STYLE_PREFIX + prompt

    try:
        # Create prediction
        resp = requests.post(
            REPLICATE_URL,
            headers={
                "Authorization": f"Bearer {REPLICATE_API_TOKEN}",
                "Content-Type": "application/json"
            },
            json={
                "version": "7762fd07cf82c948538e41f63f77d685e02b063e37e496e96eefd46c929f9bdc",
                "input": {
                    "prompt": styled_prompt,
                    "negative_prompt": "blurry, low quality, distorted, deformed, cartoon, anime, illustration, duplicate characters, mirrored, split image, collage, multiple views, diptych, side by side comparison",
                    "width": 1344,
                    "height": 768,
                    "num_outputs": 1,
                    "guidance_scale": 7.5,
                    "num_inference_steps": 30
                }
            },
            timeout=30
        )

        if not resp.ok:
            print(f"❌ Replicate create error {resp.status_code}: {resp.text[:300]}")
            resp.raise_for_status()

        prediction = resp.json()
        prediction_url = prediction.get("urls", {}).get("get")

        if not prediction_url:
            print("❌ No prediction URL returned")
            return None

        # Poll for completion
        for _ in range(60):  # up to 60s
            time.sleep(1)
            poll = requests.get(
                prediction_url,
                headers={"Authorization": f"Bearer {REPLICATE_API_TOKEN}"},
                timeout=10
            )
            poll_data = poll.json()
            status = poll_data.get("status")

            if status == "succeeded":
                output = poll_data.get("output", [])
                if not output:
                    print("⚠️ SDXL returned no output")
                    return None

                # Download image from Replicate's temporary URL
                img_url = output[0]
                img_resp = requests.get(img_url, timeout=30)
                img_resp.raise_for_status()

                filename = f"{game_id}/{uuid.uuid4().hex}.png"
                client = db.get_client()
                client.storage.from_("gauntlet-media").upload(
                    filename, img_resp.content,
                    file_options={"content-type": "image/png"}
                )
                return client.storage.from_("gauntlet-media").get_public_url(filename)

            elif status == "failed":
                error = poll_data.get("error", "unknown")
                print(f"❌ SDXL prediction failed: {error}")
                return None

            elif status == "canceled":
                print("❌ SDXL prediction canceled")
                return None

        print("❌ SDXL prediction timed out after 60s")
        return None

    except Exception as e:
        print(f"❌ Image generation failed: {e}")
        return None
