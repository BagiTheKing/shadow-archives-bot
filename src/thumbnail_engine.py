"""
Thumbnail engine — 1280x720 custom thumbnails, drawn locally with Pillow.

Nothing on the channel has ever had a custom thumbnail; YouTube has been picking
an arbitrary frame. For Shorts that barely matters because the feed autoplays,
but the long-form videos are aimed at search, where the thumbnail is doing most
of the work of earning the click.

The design rules encoded here are the boring, reliable ones:
- Three or four words maximum. A sentence at thumbnail size is a grey smudge.
- One accent word, so the eye lands somewhere specific.
- Text on the left over a scrim, subject on the right. Text over a busy image is
  the single most common way an otherwise good thumbnail becomes unreadable.
- Heavy contrast and a vignette, because the thumbnail is competing at 210px
  wide in a search result, not at full size.
"""

import os

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

TW, TH = 1280, 720
MAX_BYTES = 2 * 1024 * 1024          # YouTube rejects thumbnails over 2 MB

FONTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fonts")
FONT_PATH = os.path.join(FONTS_DIR, "Anton-Regular.ttf")

ACCENT = (255, 216, 0)
WHITE = (250, 250, 250)


def _font(size):
    try:
        return ImageFont.truetype(FONT_PATH, size)
    except Exception:
        return ImageFont.load_default()


def _wrap(draw, text, font, max_width):
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if draw.textlength(trial, font=font) <= max_width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _cover(img, w, h):
    """Scale-and-crop to fill exactly w x h without distorting."""
    src_ratio, dst_ratio = img.width / img.height, w / h
    if src_ratio > dst_ratio:
        new_w = int(img.height * dst_ratio)
        img = img.crop(((img.width - new_w) // 2, 0,
                        (img.width - new_w) // 2 + new_w, img.height))
    else:
        new_h = int(img.width / dst_ratio)
        img = img.crop((0, (img.height - new_h) // 2,
                        img.width, (img.height - new_h) // 2 + new_h))
    return img.resize((w, h), Image.LANCZOS)


def _left_scrim(size, strength=238):
    """Horizontal gradient, opaque on the left, clear by ~68% across."""
    w, h = size
    mask = Image.new("L", (w, 1))
    px = mask.load()
    for x in range(w):
        t = min(1.0, x / (w * 0.68))
        px[x, 0] = int(strength * (1 - t) ** 1.5)
    return mask.resize((w, h))


def make_thumbnail(source_image: str, headline: str, output_path: str,
                   accent_line: int = -1, badge: str = "") -> str:
    """headline: 3-5 words. accent_line: which wrapped line gets the accent
    colour (-1 = last). badge: optional small tag, e.g. a year."""
    base = Image.open(source_image).convert("RGB")
    base = _cover(base, TW, TH)

    # Grade for small-size legibility: more contrast, slightly less colour.
    base = ImageEnhance.Contrast(base).enhance(1.22)
    base = ImageEnhance.Color(base).enhance(0.88)
    base = ImageEnhance.Brightness(base).enhance(0.94)

    # Vignette.
    vig = Image.new("L", (TW, TH), 0)
    ImageDraw.Draw(vig).ellipse([-TW * 0.22, -TH * 0.3, TW * 1.22, TH * 1.3], fill=255)
    vig = vig.filter(ImageFilter.GaussianBlur(TW // 8))
    base = Image.composite(base, Image.new("RGB", (TW, TH), (0, 0, 0)), vig)

    # Left scrim so the type always has something dark to sit on.
    black = Image.new("RGB", (TW, TH), (6, 6, 10))
    base = Image.composite(black, base, _left_scrim((TW, TH)))

    d = ImageDraw.Draw(base)
    margin = int(TW * 0.055)
    text_w = int(TW * 0.56)

    # Fit the headline: shrink until it fits in at most three lines.
    size = int(TH * 0.20)
    while size > int(TH * 0.085):
        font = _font(size)
        lines = _wrap(d, headline.upper(), font, text_w)
        if len(lines) <= 3:
            break
        size -= 6
    font = _font(size)
    lines = _wrap(d, headline.upper(), font, text_w)[:3]

    line_h = int(size * 1.06)
    total_h = line_h * len(lines)
    y = (TH - total_h) // 2 + int(TH * 0.02)

    hit = accent_line if accent_line >= 0 else len(lines) - 1
    for i, line in enumerate(lines):
        colour = ACCENT if i == hit else WHITE
        # Manual drop shadow: survives being scaled to 210px wide.
        d.text((margin + 4, y + i * line_h + 4), line, font=font, fill=(0, 0, 0))
        d.text((margin, y + i * line_h), line, font=font, fill=colour)

    # Accent rule above the headline.
    d.rectangle([margin, y - int(TH * 0.045), margin + int(TW * 0.10),
                 y - int(TH * 0.045) + 8], fill=ACCENT)

    if badge:
        bf = _font(int(TH * 0.052))
        bw = d.textlength(badge.upper(), font=bf)
        bx, by = margin, y + total_h + int(TH * 0.035)
        d.rectangle([bx, by, bx + bw + 28, by + int(TH * 0.082)], fill=(0, 0, 0))
        d.rectangle([bx, by, bx + 6, by + int(TH * 0.082)], fill=ACCENT)
        d.text((bx + 18, by + int(TH * 0.041)), badge.upper(), font=bf,
               fill=WHITE, anchor="lm")

    # Save under YouTube's 2 MB ceiling.
    quality = 92
    while quality >= 55:
        base.save(output_path, "JPEG", quality=quality, optimize=True)
        if os.path.getsize(output_path) <= MAX_BYTES:
            break
        quality -= 8
    return output_path


def pick_source_scene(scenes):
    """The most usable frame for a thumbnail: a real generated image, not a
    chapter card, taken from the middle of the video where the drama sits."""
    candidates = [s for s in scenes
                  if s.get("image_path") and not s.get("card_title")]
    if not candidates:
        candidates = [s for s in scenes if s.get("image_path")]
    if not candidates:
        return None
    return candidates[len(candidates) // 2]["image_path"]
