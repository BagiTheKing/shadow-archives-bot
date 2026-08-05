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

# Flux is trained around one megapixel. Asking it for a 1920x1080 frame (2.07 MP)
# is asking it to compose at twice the resolution it learned on, and the
# classic symptom is duplicated features — the extra hands and fingers that
# made the long-form frames unusable. Generating at ~1 MP and upscaling with
# Lanczos gives a coherent image at the same delivery size; the pipeline pushes
# a 1.2x Ken Burns zoom at most, so the resample is invisible in motion.
GEN_TARGET_PIXELS = 1_050_000

# Pollinations' `enhance` runs the prompt through an LLM before generating.
# That helps a three-word prompt and hurts these: every prompt here already
# carries a shot type, an era and a deliberate style suffix, and the rewrite
# routinely drops the style. Off by default, still switchable.
ENHANCE = os.getenv("POLLINATIONS_ENHANCE", "0") == "1"

# Identify the client. Anonymous requests from CI ranges are the first thing a
# free service sheds when it is under load.
GEN_HEADERS = {
    "User-Agent": ("ArchiveParadoxBot/1.1 "
                   "(https://github.com/BagiTheKing/shadow-archives-bot)"),
}

# Raise this and every generated frame costs another five attempts. Five
# variants cycle across five attempts, so a real outage is detected but a
# transient 500 no longer produces a black screen.
GEN_MAX_RETRIES = 5


def _generation_size(width: int, height: int):
    """The nearest ~1 MP size with the requested aspect ratio, rounded to the
    multiples of 16 that diffusion models expect."""
    scale = (GEN_TARGET_PIXELS / (width * height)) ** 0.5
    if scale >= 1.0:
        return width, height          # already at or under native — leave it
    gen_w = max(512, int(round(width * scale / 16)) * 16)
    gen_h = max(512, int(round(height * scale / 16)) * 16)
    return gen_w, gen_h


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


def download_image(prompt: str, output_path: str, max_retries: int = GEN_MAX_RETRIES,
                   width: int = WIDTH, height: int = HEIGHT) -> bool:
    """Defaults to the 1080x1920 Shorts frame. The long-form pipeline passes
    16:9 dimensions instead — a vertical eight-minute documentary is a bad watch
    on desktop and TV, where search traffic actually lands."""
    encoded = urllib.parse.quote(prompt)
    seed = random.randint(0, 999_999)  # new seed per scene -> no duplicate frames
    gen_w, gen_h = _generation_size(width, height)

    # A live run once returned HTTP 500 to all thirty generation requests,
    # because every attempt reused one parameter combination. Each attempt now
    # uses a different one — falling back through models and finally to
    # Pollinations' defaults — so a single unhappy option cannot cost the video
    # every generated frame.
    variants = [
        f"?width={gen_w}&height={gen_h}&model=flux&nologo=true"
        f"&enhance={'true' if ENHANCE else 'false'}&seed={seed}",
        f"?width={gen_w}&height={gen_h}&model=flux&nologo=true&seed={seed}",
        f"?width={gen_w}&height={gen_h}&nologo=true&seed={seed}",
        f"?width={gen_w}&height={gen_h}&model=turbo&nologo=true&seed={seed}",
        f"?nologo=true&seed={seed}",
    ]

    for attempt in range(1, max_retries + 1):
        api_url = (f"https://image.pollinations.ai/prompt/{encoded}"
                   f"{variants[(attempt - 1) % len(variants)]}")
        try:
            response = requests.get(api_url, timeout=90, headers=GEN_HEADERS)
            if response.status_code == 200 and _is_valid_image(response.content):
                img = Image.open(io.BytesIO(response.content)).convert("RGB")
                if (img.width, img.height) != (width, height):
                    img = img.resize((width, height), Image.LANCZOS)
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


def make_fallback_image(output_path: str, seed: int = 0,
                        width: int = WIDTH, height: int = HEIGHT):
    """A moody dark-vignette gradient. Ugly-proof: if a download fails, the scene
    becomes an atmospheric 'black screen' moment instead of a broken frame.
    Dimensions are parameterised so the 16:9 long-form path is not handed a
    portrait frame to stretch."""
    rng = random.Random(seed)
    short_side = min(width, height)
    img = Image.new("RGB", (width, height), (8, 8, 12))
    draw = ImageDraw.Draw(img)
    # faint smoky blobs
    for _ in range(6):
        x, y = rng.randint(0, width), rng.randint(0, height)
        r = rng.randint(short_side // 5, short_side // 2)
        tone = rng.randint(18, 38)
        draw.ellipse([x - r, y - r, x + r, y + r], fill=(tone, tone, tone + 6))
    img = img.filter(ImageFilter.GaussianBlur(short_side // 9))
    # vignette
    mask = Image.new("L", (width, height), 0)
    pad = short_side // 4
    ImageDraw.Draw(mask).ellipse([-pad, -pad, width + pad, height + pad], fill=90)
    mask = mask.filter(ImageFilter.GaussianBlur(short_side // 5))
    black = Image.new("RGB", (width, height), (0, 0, 0))
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
