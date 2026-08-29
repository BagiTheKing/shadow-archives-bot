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
                   accent_line: int = -1, badge: str = "",
                   accent: tuple = None) -> str:
    """headline: 3-5 words. accent_line: which wrapped line gets the accent
    colour (-1 = last). badge: optional small tag, e.g. a year.

    accent overrides the channel colour, and is how the three episode formats
    are told apart at thumbnail size: amber for THE ORDER, red for THE DESIGN,
    cyan for THE UNSOLVED. Three of a format in a row should read as a set.
    """
    accent = accent or ACCENT
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
        colour = accent if i == hit else WHITE
        # Manual drop shadow: survives being scaled to 210px wide.
        d.text((margin + 4, y + i * line_h + 4), line, font=font, fill=(0, 0, 0))
        d.text((margin, y + i * line_h), line, font=font, fill=colour)

    # Accent rule above the headline.
    d.rectangle([margin, y - int(TH * 0.045), margin + int(TW * 0.10),
                 y - int(TH * 0.045) + 8], fill=accent)

    if badge:
        bf = _font(int(TH * 0.052))
        bw = d.textlength(badge.upper(), font=bf)
        bx, by = margin, y + total_h + int(TH * 0.035)
        d.rectangle([bx, by, bx + bw + 28, by + int(TH * 0.082)], fill=(0, 0, 0))
        d.rectangle([bx, by, bx + 6, by + int(TH * 0.082)], fill=accent)
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
    """The most usable frame for a thumbnail, from the middle of the video
    where the drama sits. A real archival photograph is preferred over a
    generated frame: on a history channel, authenticity earns the click."""
    # Never the outro: that frame is the NEXT episode's card, so using it would
    # put another video's title on this one's thumbnail.
    with_images = [s for s in scenes
                   if s.get("image_path") and not s.get("is_outro")]
    candidates = [s for s in with_images if s.get("is_real")] or with_images
    if not candidates:
        return None
    return candidates[len(candidates) // 2]["image_path"]


# ---------------------------------------------------------------------------
# The next-file card
#
# The outro used to be words only: the narrator said "Next file: ..." over the
# last frame of the story just told, while the picture carried on doing its slow
# zoom. Watching it back, there was nothing to tell you the video had stopped
# being about Cartagena and started advertising the next one — it read as one
# more sentence of the same story. A trailer needs a cut.
#
# So the outro is now its own scene with its own card: new image, a NEXT FILE
# label, the subject in headline type, and the tease under it. The viewer sees
# the video change subject at the moment the narrator does.

CARD_W, CARD_H = 1080, 1920

# Where the type sits, as a fraction of card height, and how much scrim each
# band needs. Heavy through the headline and tease, easing off below the
# subscribe line so the bottom of the picture is properly visible.
_CARD_SCRIM_STOPS = ((0.00, 0.42), (0.17, 0.60), (0.50, 0.60),
                     (0.68, 0.38), (1.00, 0.10))


def _card_scrim(size):
    """Vertical gradient mask for the next-file card, from _CARD_SCRIM_STOPS.

    A flat blend cannot do this job: enough darkness to carry a headline turns
    the whole frame to mud, and enough light to show the picture loses the
    type. Weighting it by where the words actually are gives both.
    """
    w, h = size
    mask = Image.new("L", (1, h))
    px = mask.load()
    for y in range(h):
        t = y / max(h - 1, 1)
        for (t0, a0), (t1, a1) in zip(_CARD_SCRIM_STOPS, _CARD_SCRIM_STOPS[1:]):
            if t <= t1 or t1 == 1.0:
                span = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
                px[0, y] = int(255 * (a0 + (a1 - a0) * min(max(span, 0.0), 1.0)))
                break
    return mask.resize((w, h))


def _fit_lines(d, text, max_width, start, floor, max_lines):
    """Largest font size at which `text` wraps into at most max_lines."""
    size = start
    while size > floor:
        font = _font(size)
        if len(_wrap(d, text, font, max_width)) <= max_lines:
            return font, _wrap(d, text, font, max_width)
        size -= 4
    font = _font(floor)
    return font, _wrap(d, text, font, max_width)[:max_lines]


def make_next_file_card(source_image: str, subject: str, tease: str,
                        output_path: str, accent: tuple = None,
                        badge: str = "", label: str = "NEXT FILE",
                        cta: str = "") -> str:
    """Draws the 1080x1920 card shown while the narrator teases the next video.

    subject: the next story's name, in headline type ("The Radcliffe Line").
    tease:   the spoken clause, set smaller underneath, in sentence case.
    """
    accent = accent or ACCENT
    base = Image.open(source_image).convert("RGB")
    base = _cover(base, CARD_W, CARD_H)

    # The first version of this graded the picture into oblivion — colour 0.45,
    # brightness 0.42, a 6px blur AND a 35% black blend on top — so the card
    # played as white type on a black screen and the frame the generator had
    # actually produced was invisible. The image is the difference between a
    # title card and a trailer, so it stays legible: a light touch here, and
    # contrast for the type comes from the gradient below instead.
    base = ImageEnhance.Color(base).enhance(0.85)
    base = ImageEnhance.Contrast(base).enhance(1.06)
    base = ImageEnhance.Brightness(base).enhance(0.88)
    # Just enough blur to stop fine detail fighting the headline. 1.5, not 2.5:
    # the type is stroked now and carries itself, and at 2.5 a sharp frame from
    # the good generator came out looking like the cheap fallback.
    base = base.filter(ImageFilter.GaussianBlur(1.5))

    # Scrim weighted to the top, where the type is, and lifting toward the
    # bottom so the lower third of the picture reads clearly.
    black = Image.new("RGB", (CARD_W, CARD_H), (5, 5, 8))
    base = Image.composite(black, base, _card_scrim((CARD_W, CARD_H)))

    # Vignette: pulls the corners down so the frame feels composed rather than
    # cropped, and keeps the eye on the centre column of type.
    vig = Image.new("L", (CARD_W, CARD_H), 0)
    ImageDraw.Draw(vig).ellipse(
        [-CARD_W * 0.30, -CARD_H * 0.12, CARD_W * 1.30, CARD_H * 1.12], fill=255)
    vig = vig.filter(ImageFilter.GaussianBlur(CARD_W // 7))
    base = Image.composite(base, Image.new("RGB", (CARD_W, CARD_H), (0, 0, 0)),
                           vig)

    d = ImageDraw.Draw(base)
    margin = int(CARD_W * 0.085)
    text_w = CARD_W - margin * 2

    # The type block sits above centre, clear of the Shorts UI at the bottom.
    subject_font, subject_lines = _fit_lines(
        d, subject.upper(), text_w, int(CARD_H * 0.075), int(CARD_H * 0.035), 3)
    subject_lh = int(subject_font.size * 1.08)

    tease_font, tease_lines = _fit_lines(
        d, tease, text_w, int(CARD_H * 0.032), int(CARD_H * 0.020), 3)
    tease_lh = int(tease_font.size * 1.28)

    label_font = _font(int(CARD_H * 0.030))
    label_h = int(CARD_H * 0.075)

    block_h = (label_h + subject_lh * len(subject_lines)
               + int(CARD_H * 0.035) + tease_lh * len(tease_lines))
    y = int(CARD_H * 0.34) - block_h // 2

    # Label: an accent rule, then the words, letter-spaced by hand because
    # Pillow has no tracking control.
    d.rectangle([margin, y, margin + int(CARD_W * 0.16), y + 8], fill=accent)
    lx, ly = margin, y + int(CARD_H * 0.028)
    for ch in label.upper():
        d.text((lx, ly), ch, font=label_font, fill=accent,
               stroke_width=max(2, int(label_font.size * 0.05)),
               stroke_fill=(0, 0, 0))
        # textlength ignores the stroke, so the tracking stays even.
        lx += d.textlength(ch, font=label_font) + int(CARD_W * 0.009)
    y += label_h

    # Stroke, not just an offset shadow. The picture behind this is a different
    # image every episode and an offset shadow only protects one side of a
    # letter — over a bright patch the other side disappears. An outline holds
    # the type on anything the generator produces.
    for line in subject_lines:
        d.text((margin + 5, y + 5), line, font=subject_font, fill=(0, 0, 0))
        d.text((margin, y), line, font=subject_font, fill=WHITE,
               stroke_width=max(2, int(subject_font.size * 0.045)),
               stroke_fill=(0, 0, 0))
        y += subject_lh

    y += int(CARD_H * 0.035)
    for line in tease_lines:
        d.text((margin + 3, y + 3), line, font=tease_font, fill=(0, 0, 0))
        d.text((margin, y), line, font=tease_font, fill=(228, 228, 232),
               stroke_width=max(2, int(tease_font.size * 0.05)),
               stroke_fill=(0, 0, 0))
        y += tease_lh

    if badge:
        bf = _font(int(CARD_H * 0.024))
        by = y + int(CARD_H * 0.030)
        bw = d.textlength(badge.upper(), font=bf)
        d.rectangle([margin, by, margin + bw + 34, by + int(CARD_H * 0.042)],
                    fill=(0, 0, 0))
        d.rectangle([margin, by, margin + 6, by + int(CARD_H * 0.042)],
                    fill=accent)
        d.text((margin + 22, by + int(CARD_H * 0.021)), badge.upper(),
               font=bf, fill=WHITE, anchor="lm")

    # The subscribe ask is drawn HERE rather than left to the burned-in ASS
    # overlay, which is positioned from the bottom of the frame and landed on
    # top of the badge. Drawn into the card it can be placed against the type
    # above it, and video_engine suppresses the overlay whenever a card exists.
    if cta:
        cf = _font(int(CARD_H * 0.028))
        cy = int(CARD_H * 0.66)
        d.rectangle([margin, cy - int(CARD_H * 0.004),
                     margin + int(CARD_W * 0.16), cy + 2], fill=accent)
        d.text((margin + 3, cy + int(CARD_H * 0.030) + 3), cta.upper(),
               font=cf, fill=(0, 0, 0))
        d.text((margin, cy + int(CARD_H * 0.030)), cta.upper(),
               font=cf, fill=accent,
               stroke_width=max(2, int(cf.size * 0.05)), stroke_fill=(0, 0, 0))

    base.save(output_path, "JPEG", quality=90, optimize=True)
    return output_path
