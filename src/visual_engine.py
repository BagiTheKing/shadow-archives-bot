"""
Visual engine — Pollinations with the `flux` model (their best free model),
full 1080x1920 resolution, real image validation, and a designed fallback frame
so a failed download never kills the video or shows an error page as a "photo".
"""

import io
import os
import random
import time
import urllib.parse

import requests
from PIL import Image, ImageDraw, ImageFilter

WIDTH, HEIGHT = 1080, 1920


def _is_valid_image(content: bytes) -> bool:
    """Pollinations sometimes returns HTML error pages with status 200.
    Verify the bytes actually decode as a real image of a sane size."""
    try:
        img = Image.open(io.BytesIO(content))
        img.verify()
        img = Image.open(io.BytesIO(content))
        return img.width >= 512 and img.height >= 512
    except Exception:
        return False


def download_image(prompt: str, output_path: str, max_retries: int = 4) -> bool:
    encoded = urllib.parse.quote(prompt)
    seed = random.randint(0, 999_999)  # new seed per scene -> no duplicate frames
    api_url = (
        f"https://image.pollinations.ai/prompt/{encoded}"
        f"?width={WIDTH}&height={HEIGHT}&model=flux&nologo=true&enhance=true&seed={seed}"
    )

    for attempt in range(1, max_retries + 1):
        try:
            response = requests.get(api_url, timeout=90)
            if response.status_code == 200 and _is_valid_image(response.content):
                img = Image.open(io.BytesIO(response.content)).convert("RGB")
                if (img.width, img.height) != (WIDTH, HEIGHT):
                    img = img.resize((WIDTH, HEIGHT), Image.LANCZOS)
                img.save(output_path, "JPEG", quality=92)
                return True
            if response.status_code == 429:
                wait = 15 * attempt
                print(f"⚠️ Rate limited. Waiting {wait}s (attempt {attempt}/{max_retries})...")
                time.sleep(wait)
            else:
                print(f"⚠️ Bad response ({response.status_code} or invalid image). Retrying...")
                time.sleep(8)
        except Exception as e:
            print(f"⚠️ Network error: {e}. Retrying in 8s...")
            time.sleep(8)
    return False


def make_fallback_image(output_path: str, seed: int = 0):
    """A moody dark-vignette gradient. Ugly-proof: if a download fails, the scene
    becomes an atmospheric 'black screen' moment instead of a broken frame."""
    rng = random.Random(seed)
    img = Image.new("RGB", (WIDTH, HEIGHT), (8, 8, 12))
    draw = ImageDraw.Draw(img)
    # faint smoky blobs
    for _ in range(6):
        x, y = rng.randint(0, WIDTH), rng.randint(0, HEIGHT)
        r = rng.randint(200, 600)
        tone = rng.randint(18, 38)
        draw.ellipse([x - r, y - r, x + r, y + r], fill=(tone, tone, tone + 6))
    img = img.filter(ImageFilter.GaussianBlur(120))
    # vignette
    mask = Image.new("L", (WIDTH, HEIGHT), 0)
    ImageDraw.Draw(mask).ellipse([-300, -300, WIDTH + 300, HEIGHT + 300], fill=90)
    mask = mask.filter(ImageFilter.GaussianBlur(200))
    black = Image.new("RGB", (WIDTH, HEIGHT), (0, 0, 0))
    img = Image.composite(img, black, mask)
    img.save(output_path, "JPEG", quality=92)


def generate_visual_pipeline(script_data: dict, output_dir: str = "build") -> dict:
    print("🎨 Initializing Visual Engine (flux @ 1080x1920)...")
    os.makedirs(output_dir, exist_ok=True)

    scenes = script_data.get("scenes", [])
    for idx, scene in enumerate(scenes):
        image_path = os.path.join(output_dir, f"scene_{idx}.jpg")
        prompt = scene.get("image_prompt", "dark atmospheric historical scene, cinematic")

        print(f"🖼️ Generating image for scene {idx}...")
        if download_image(prompt, image_path):
            print(f"📸 Saved {image_path}")
        else:
            print(f"⚠️ Scene {idx}: all attempts failed — using designed fallback frame.")
            make_fallback_image(image_path, seed=idx)
        scene["image_path"] = image_path

        if idx < len(scenes) - 1:
            time.sleep(6)  # stay under Pollinations' anonymous rate limit

    print("✅ Visual pipeline complete.")
    return script_data
