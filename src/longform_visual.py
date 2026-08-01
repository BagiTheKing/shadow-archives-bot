"""
Long-form visual engine — 16:9, not the Shorts 9:16.

Three differences from visual_engine.py:

1. LANDSCAPE. The Shorts pipeline renders 1080x1920. This one renders 1920x1080,
   because the deep dive is aimed at search traffic, which lands on desktop and
   TV where a vertical eight-minute documentary is a bad watch.

2. Frames are saved pre-upscaled at KB_W x KB_H. The Shorts renderer runs a
   scale filter inside the ffmpeg chain, re-scaling every frame at render time to
   stop zoompan jitter. Over eight minutes that is the most expensive thing in
   the pipeline. Doing the upscale once here, in Pillow, removes the filter and
   costs about a second per image.

3. The first frame of each chapter is a locally drawn chapter card instead of a
   generated image — six fewer Pollinations requests per video, where the API is
   the main rate-limit risk, and it gives the viewer a structural beat.
"""

import os
import time

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from visual_engine import download_image, make_fallback_image

# Delivery resolution for the long-form video.
LF_W, LF_H = 1920, 1080

# 1.5x delivery: enough headroom for a 1.2x Ken Burns zoom to stay above 1080p,
# without the 4x-area cost of the Shorts path.
KB_W, KB_H = int(LF_W * 1.5), int(LF_H * 1.5)

FONTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fonts")
FONT_PATH = os.path.join(FONTS_DIR, "Anton-Regular.ttf")

ACCENT = (255, 232, 0)          # matches the caption accent in the Shorts
THROTTLE_SECONDS = 6            # Pollinations anonymous rate limit


def _font(size: int):
    try:
        return ImageFont.truetype(FONT_PATH, size)
    except Exception:
        return ImageFont.load_default()


def _wrap(draw, text, font, max_width):
    words, lines, current = text.split(), [], ""
    for w in words:
        trial = f"{current} {w}".strip()
        if draw.textlength(trial, font=font) <= max_width or not current:
            current = trial
        else:
            lines.append(current)
            current = w
    if current:
        lines.append(current)
    return lines


def make_chapter_card(heading: str, number: int, output_path: str):
    """A dark title card: chapter number, rule, heading. Drawn locally, so it
    costs no API call and never rate-limits."""
    img = Image.new("RGB", (KB_W, KB_H), (9, 9, 13))
    d = ImageDraw.Draw(img)

    # Soft smoky backdrop so the card does not read as a flat colour block.
    for i in range(5):
        x, y = KB_W * (0.2 + 0.15 * i), KB_H * (0.25 + 0.12 * i)
        r = KB_W * 0.28
        tone = 20 + i * 4
        d.ellipse([x - r, y - r, x + r, y + r], fill=(tone, tone, tone + 8))
    img = img.filter(ImageFilter.GaussianBlur(KB_W // 14))
    d = ImageDraw.Draw(img)

    num_font = _font(int(KB_H * 0.075))
    head_font = _font(int(KB_H * 0.105))
    cx, cy = KB_W // 2, KB_H // 2
    margin = int(KB_W * 0.10)

    d.text((cx, cy - int(KB_H * 0.20)), f"{number:02d}", font=num_font,
           fill=ACCENT, anchor="mm")

    rule_w = int(KB_W * 0.09)
    ry = cy - int(KB_H * 0.12)
    d.line([(cx - rule_w, ry), (cx + rule_w, ry)], fill=(90, 90, 96), width=4)

    lines = _wrap(d, heading.upper(), head_font, KB_W - 2 * margin)
    line_h = int(KB_H * 0.125)
    start_y = cy - ((len(lines) - 1) * line_h) // 2 + int(KB_H * 0.04)
    for i, line in enumerate(lines):
        d.text((cx, start_y + i * line_h), line, font=head_font,
               fill=(245, 245, 245), anchor="mm")

    img.save(output_path, "JPEG", quality=92)


def _to_kb_size(path: str):
    """Upscale a downloaded frame to the Ken Burns working size, once."""
    img = Image.open(path).convert("RGB")
    if (img.width, img.height) != (KB_W, KB_H):
        img = img.resize((KB_W, KB_H), Image.LANCZOS)
    img.save(path, "JPEG", quality=92)


def generate_longform_visuals(scenes: list, output_dir: str = "build") -> list:
    print(f"🎨 Long-form visual engine ({LF_W}x{LF_H}, working at {KB_W}x{KB_H})...")
    os.makedirs(output_dir, exist_ok=True)

    api_calls = 0
    for idx, scene in enumerate(scenes):
        path = os.path.join(output_dir, f"lf_scene_{idx:03d}.jpg")

        if scene.get("card_title"):
            print(f"🗂️  Scene {idx}: chapter card — {scene['card_title']}")
            make_chapter_card(scene["card_title"], scene.get("card_number", 1), path)
            scene["image_path"] = path
            continue

        if api_calls:
            time.sleep(THROTTLE_SECONDS)

        print(f"🖼️  Scene {idx}: generating image (call {api_calls + 1})...")
        prompt = scene.get("image_prompt") or "wide establishing shot, dark historical scene"
        if download_image(prompt, path, width=LF_W, height=LF_H):
            _to_kb_size(path)
        else:
            print(f"⚠️  Scene {idx}: generation failed — designed fallback frame.")
            make_fallback_image(path, seed=idx, width=LF_W, height=LF_H)
            _to_kb_size(path)
        scene["image_path"] = path
        api_calls += 1

    cards = sum(1 for s in scenes if s.get("card_title"))
    print(f"✅ Visuals complete: {len(scenes)} frames "
          f"({api_calls} generated, {cards} chapter cards).")
    return scenes
