"""Replicate image generation — SDXL for portraits, IP-Adapter for scenes."""

import os
import time
import requests
import uuid
import db

REPLICATE_API_TOKEN = os.environ.get('REPLICATE_API_TOKEN')
REPLICATE_URL = "https://api.replicate.com/v1/predictions"

IMAGE_STYLE_PREFIX = "Photorealistic, dramatic cinematic lighting, high detail. "
NEGATIVE_PROMPT = "blurry, low quality, distorted, deformed, cartoon, anime, illustration, duplicate characters, mirrored, split image, collage, multiple views, diptych"

# SDXL for plain generation (portraits)
SDXL_VERSION = "7762fd07cf82c948538e41f63f77d685e02b063e37e496e96eefd46c929f9bdc"
# IP-Adapter Face SDXL for consistent characters in scenes
IP_ADAPTER_VERSION = "77a68a2370a1ca65c580ff828d577a224f7e5951d7f26b5697938e4cfe3e02be"


def _run_prediction(payload: dict) -> str | None:
    """Submit a Replicate prediction and poll for result. Returns output URL or None."""
    try:
        resp = requests.post(
            REPLICATE_URL,
            headers={
                "Authorization": f"Bearer {REPLICATE_API_TOKEN}",
                "Content-Type": "application/json"
            },
            json=payload,
            timeout=30
        )
        if not resp.ok:
            print(f"❌ Replicate error {resp.status_code}: {resp.text[:300]}")
            return None

        prediction_url = resp.json().get("urls", {}).get("get")
        if not prediction_url:
            return None

        for _ in range(90):  # up to 90s
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
                if isinstance(output, list) and output:
                    return output[0]
                elif isinstance(output, str):
                    return output
                return None
            elif status in ("failed", "canceled"):
                print(f"❌ Prediction {status}: {poll_data.get('error', '?')}")
                return None

        print("❌ Prediction timed out after 90s")
        return None

    except Exception as e:
        print(f"❌ Replicate request failed: {e}")
        return None


def _upload_to_storage(image_url: str, game_id: str) -> str | None:
    """Download image from temporary URL and upload to Supabase Storage."""
    try:
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
        print(f"❌ Upload failed: {e}")
        return None


def generate_portrait(description: str, game_id: str) -> str | None:
    """Generate a character portrait headshot via SDXL. Returns public URL."""
    if not REPLICATE_API_TOKEN:
        return None

    prompt = f"Portrait headshot, photorealistic, studio lighting, neutral background. {description}"

    img_url = _run_prediction({
        "version": SDXL_VERSION,
        "input": {
            "prompt": prompt,
            "negative_prompt": NEGATIVE_PROMPT,
            "width": 768,
            "height": 768,
            "num_outputs": 1,
            "guidance_scale": 7.5,
            "num_inference_steps": 30
        }
    })

    if not img_url:
        return None
    return _upload_to_storage(img_url, game_id)


def generate_image(prompt: str, game_id: str, face_urls: list[str] = None) -> str | None:
    """Generate scene image. Uses IP-Adapter if face reference URLs provided, plain SDXL otherwise."""
    if not REPLICATE_API_TOKEN:
        print("⚠️ REPLICATE_API_TOKEN not set — skipping image generation")
        return None

    styled_prompt = IMAGE_STYLE_PREFIX + prompt

    if face_urls:
        # IP-Adapter: pass face references for character consistency
        input_data = {
            "prompt": styled_prompt,
            "negative_prompt": NEGATIVE_PROMPT,
            "width": 1344,
            "height": 768,
            "num_outputs": 1,
            "guidance_scale": 7.5,
            "num_inference_steps": 30,
            "ip_adapter_scale": 0.6,
            "image": face_urls[0],  # Primary face reference
        }
        version = IP_ADAPTER_VERSION
    else:
        # Plain SDXL fallback
        input_data = {
            "prompt": styled_prompt,
            "negative_prompt": NEGATIVE_PROMPT,
            "width": 1344,
            "height": 768,
            "num_outputs": 1,
            "guidance_scale": 7.5,
            "num_inference_steps": 30
        }
        version = SDXL_VERSION

    img_url = _run_prediction({"version": version, "input": input_data})
    if not img_url:
        return None
    return _upload_to_storage(img_url, game_id)
