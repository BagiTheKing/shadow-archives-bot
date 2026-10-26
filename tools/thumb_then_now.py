"""
NOW / THEN thumbnails (owner, 26 Oct: "take something from the news and use
it as context for what happened in the past, so people click to see the
now and then"): today's picture in colour on the left, the past in black
and white on the right, a torn orange seam, and the paradox in a few huge
words. Every number must be one the narration states.

    python tools/thumb_then_now.py now.jpg then.jpg out_dir

Writes out_dir/thumb_a.jpg, thumb_b.jpg, thumb_c.jpg (1280x720).
"""

import os
import random
import sys

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

W, H = 1280, 720
ANTON = os.path.join(os.path.dirname(__file__), "..", "fonts", "Anton-Regular.ttf")
ORANGE, RED, YELLOW, WHITE = (255, 150, 40), (230, 30, 30), (255, 214, 0), (255, 255, 255)


def font(size):
    return ImageFont.truetype(ANTON, size)


def cover(img, w, h, focus_x=0.5, focus_y=0.4):
    """Fills w x h, cropping around a focus point (faces sit high)."""
    s = max(w / img.width, h / img.height)
    img = img.resize((int(img.width * s) + 1, int(img.height * s) + 1), Image.LANCZOS)
    x = int((img.width - w) * focus_x)
    y = int((img.height - h) * focus_y)
    return img.crop((x, y, x + w, y + h))


def tear_mask(seam_x, slant, seed=7):
    """White left of a jagged, slanted tear line: today's side."""
    rnd = random.Random(seed)
    pts, y = [], 0
    while y <= H:
        x = seam_x + slant * (0.5 - y / H) + rnd.randint(-14, 14)
        pts.append((x, y))
        y += 24
    mask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(mask).polygon([(0, 0)] + pts + [(0, H)], fill=255)
    return mask, pts


def place(img, focus, target, zoom):
    """The picture scaled to zoom x the frame's height, cropped so its
    subject (focus, as fractions of the picture) lands on target (pixels)."""
    s = H * zoom / img.height
    img = img.resize((int(img.width * s) + 1, int(img.height * s) + 1), Image.LANCZOS)
    x = min(max(0, int(focus[0] * img.width - target[0])), img.width - W)
    y = min(max(0, int(focus[1] * img.height - target[1])), img.height - H)
    return img.crop((x, y, x + W, y + H))


# where each picture's subject is: Netanyahu's face, Dimona's reactor tower
NOW_FOCUS = (0.48, 0.30)
THEN_FOCUS = (0.44, 0.60)


def base(now, then, seam_x=640, slant=120):
    left = place(now, NOW_FOCUS, (300, 230), 1.35)
    left = ImageEnhance.Contrast(ImageEnhance.Color(left).enhance(1.25)).enhance(1.15)
    right = place(then, THEN_FOCUS, (930, 360), 2.0)
    right = ImageOps.grayscale(right).convert("RGB")
    right = ImageEnhance.Contrast(right).enhance(1.6)
    right = ImageEnhance.Brightness(right).enhance(0.85)
    mask, pts = tear_mask(seam_x, slant)
    img = Image.composite(left, right, mask)
    # the tear: an orange glow under a bright edge
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(glow).line(pts, fill=ORANGE + (255,), width=26)
    img = Image.alpha_composite(img.convert("RGBA"), glow.filter(ImageFilter.GaussianBlur(10)))
    ImageDraw.Draw(img).line(pts, fill=(255, 220, 160, 255), width=6)
    # vignette, so the words read on anything
    vig = Image.new("L", (W, H), 0)
    ImageDraw.Draw(vig).ellipse((-W * 0.25, -H * 0.35, W * 1.25, H * 1.35), fill=255)
    vig = vig.filter(ImageFilter.GaussianBlur(120))
    dark = Image.new("RGBA", (W, H), (0, 0, 0, 255))
    img = Image.composite(img, Image.alpha_composite(img, Image.blend(dark, img, 0.35)), vig)
    return img


def text(img, xy, words, size, fill, anchor="la", stroke=8, bg=None):
    d = ImageDraw.Draw(img)
    f = font(size)
    if bg:
        box = d.textbbox(xy, words, font=f, anchor=anchor)
        d.rectangle((box[0] - 18, box[1] - 10, box[2] + 18, box[3] + 14), fill=bg)
        d.text(xy, words, font=f, fill=fill, anchor=anchor)
    else:
        d.text(xy, words, font=f, fill=fill, anchor=anchor,
               stroke_width=stroke, stroke_fill=(0, 0, 0))


def tag(img, xy, words, anchor="la"):
    d = ImageDraw.Draw(img)
    f = font(46)
    box = d.textbbox(xy, words, font=f, anchor=anchor)
    d.rectangle((box[0] - 14, box[1] - 8, box[2] + 14, box[3] + 10), fill=(0, 0, 0, 200))
    d.rectangle((box[0] - 14, box[1] - 8, box[0] - 6, box[3] + 10), fill=ORANGE)
    d.text(xy, words, font=f, fill=WHITE, anchor=anchor)


def variants(now, then, out):
    os.makedirs(out, exist_ok=True)
    # A: the two claims side by side
    a = base(now, then)
    tag(a, (40, 34), "TODAY")
    tag(a, (W - 40, 34), "1960", anchor="ra")
    text(a, (40, H - 140), "NO BOMB", 118, WHITE, anchor="ls")
    text(a, (40, H - 40), "FOR IRAN", 84, YELLOW, anchor="ls")
    text(a, (W - 40, H - 140), "100+", 150, RED, anchor="rs")
    text(a, (W - 40, H - 40), "FOR ISRAEL", 84, WHITE, anchor="rs")
    a.convert("RGB").save(os.path.join(out, "thumb_a.jpg"), quality=92)

    # B: one question across the seam
    b = base(now, then)
    tag(b, (40, 34), "TODAY")
    tag(b, (W - 40, 34), "1960", anchor="ra")
    text(b, (W // 2, H - 150), "WHO REALLY", 112, WHITE, anchor="ms")
    text(b, (W // 2, H - 40), "HAS THE BOMB?", 112, YELLOW, anchor="ms")
    b.convert("RGB").save(os.path.join(out, "thumb_b.jpg"), quality=92)

    # C: what he says, what was built
    c = base(now, then, seam_x=600)
    text(c, (36, 120), "“IRAN MUST", 92, WHITE, anchor="ls")
    text(c, (36, 222), "NEVER...”", 92, WHITE, anchor="ls")
    text(c, (W - 36, H - 150), "BUILT IN", 92, WHITE, anchor="rs")
    text(c, (W - 36, H - 44), "SECRET", 120, RED, anchor="rs")
    tag(c, (W - 40, 34), "1960", anchor="ra")
    c.convert("RGB").save(os.path.join(out, "thumb_c.jpg"), quality=92)


if __name__ == "__main__":
    now_p, then_p, out_dir = sys.argv[1:4]
    variants(Image.open(now_p).convert("RGB"), Image.open(then_p).convert("RGB"), out_dir)
    print(f"✅ Thumbnails in {out_dir}")
