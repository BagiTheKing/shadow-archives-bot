"""
Chain of consequences: one per long-form video, in "The Chain".

Approved from the two-maps/chain preview (Kinmen 1958). Numbered boxes
appear one by one as each step is spoken, joined by red arrows that draw
themselves: the decision first (outlined red) and what it set off. The
newest step is lit and earlier ones step back, so the eye follows the
narration. Background: the scene's own photo, blurred and darkened in the
chapter's tone, so the graphic sits inside the film rather than on a slide.

The steps come from the script step (longform_llm._place_chain), where
every meaningful word of every step must already be in the narration.
Like the map, any failure returns None and the scene keeps its photos.
"""

import math
import os
import re
import shutil
import subprocess
import tempfile

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageEnhance

W, H, FPS = 1920, 1080, 30
SS = 2
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANTON = os.path.join(BASE, "fonts", "Anton-Regular.ttf")
MONO = os.path.join(BASE, "fonts", "FreeMonoBold.ttf")
RED = (205, 58, 46)
TONES = {  # background tint and box text colour per chapter tone
    "cold": ((70, 90, 110), (226, 232, 238)),
    "warm": ((110, 90, 65), (238, 230, 212)),
    "neutral": ((80, 80, 80), (232, 232, 226)),
}


def ease(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


def wrap(draw, text, font, width):
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if cur and draw.textlength(trial, font=font) > width:
            lines.append(cur)
            cur = w
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


def background(img_path, tone):
    tint, _ = TONES[tone]
    im = Image.open(img_path).convert("RGB")
    r = max(W * SS / im.width, H * SS / im.height)
    im = im.resize((int(im.width * r) + 1, int(im.height * r) + 1))
    x, y = (im.width - W * SS) // 2, (im.height - H * SS) // 2
    im = im.crop((x, y, x + W * SS, y + H * SS))
    im = im.filter(ImageFilter.GaussianBlur(18 * SS))
    im = ImageEnhance.Color(im).enhance(0.35)
    im = ImageEnhance.Brightness(im).enhance(0.32)
    return Image.blend(im, Image.new("RGB", im.size, tint), 0.12)


def render(steps, bg_path, out_mp4, duration, tone="cold"):
    _, text_col = TONES[tone]
    bg = background(bg_path, tone)
    head = ImageFont.truetype(ANTON, 50 * SS)
    num = ImageFont.truetype(MONO, 26 * SS)
    n = len(steps)
    margin, gap = 110 * SS, 96 * SS
    bw = (W * SS - 2 * margin - (n - 1) * gap) / n
    bh = 250 * SS
    top = (H * SS - bh) / 2 - 30 * SS
    boxes = [(margin + i * (bw + gap), top, margin + i * (bw + gap) + bw, top + bh)
             for i in range(n)]

    anim_end = min(duration, steps[-1]["at"] + 2.2)
    frames = int(round(anim_end * FPS))
    work = tempfile.mkdtemp(prefix="chain_")
    try:
        for fi in range(frames):
            t = fi / FPS
            # slow push-in on the background
            z = 1.0 + 0.04 * (t / max(duration, 1))
            bw_, bh_ = int(W * SS / z), int(H * SS / z)
            ox, oy = (W * SS - bw_) // 2, (H * SS - bh_) // 2
            img = bg.crop((ox, oy, ox + bw_, oy + bh_)).resize((W * SS, H * SS))
            d = ImageDraw.Draw(img, "RGBA")

            for i, st in enumerate(steps):
                a = ease((t - st["at"]) / 0.45)
                if a <= 0:
                    continue
                x0, y0, x1, y1 = boxes[i]
                dy = (1 - a) * 24 * SS
                y0, y1 = y0 + dy, y1 + dy
                first = i == 0
                # the newest step is lit; earlier ones step back as the next
                # one lands, so the eye follows the narration
                nxt = steps[i + 1]["at"] if i + 1 < n else None
                dim = 1.0 - 0.45 * ease((t - nxt) / 0.5) if nxt else 1.0
                a_txt = a * dim
                d.rectangle([x0, y0, x1, y1], fill=(0, 0, 0, int(150 * a)))
                edge = RED if first else text_col
                d.rectangle([x0, y0, x1, y1],
                            outline=edge + (int(255 * a * (1 if first else dim)),),
                            width=(3 if first else 2) * SS)
                d.text((x0 + 22 * SS, y0 + 18 * SS), f"{i + 1:02d}", font=num,
                       fill=RED + (int(255 * a),))
                lines = wrap(d, st["text"].upper(), head, bw - 44 * SS)
                lh = 60 * SS
                ty = y0 + (bh - lh * len(lines)) / 2 + 16 * SS
                for k, ln in enumerate(lines):
                    lw = d.textlength(ln, font=head)
                    d.text((x0 + (bw - lw) / 2, ty + k * lh), ln, font=head,
                           fill=text_col + (int(255 * a_txt),))

                # arrow from the previous box, drawn just before this one lands
                if i > 0:
                    fa = ease((t - (st["at"] - 0.35)) / 0.4)
                    if fa > 0:
                        px1 = boxes[i - 1][2] + 14 * SS
                        px2 = x0 - 14 * SS
                        cy = top + bh / 2
                        xe = px1 + (px2 - px1) * fa
                        d.line([(px1, cy), (xe, cy)], fill=RED, width=5 * SS)
                        if fa > 0.9:
                            L = 18 * SS
                            d.polygon([(xe + 4 * SS, cy), (xe - L, cy - L * 0.7),
                                       (xe - L, cy + L * 0.7)], fill=RED)

            # the last consequence gets a slow red underline once it lands
            la = ease((t - steps[-1]["at"] - 0.6) / 0.8)
            if la > 0:
                x0, _, x1, y1 = boxes[-1]
                ux = x0 + (x1 - x0) * la
                d.line([(x0, y1 + 18 * SS), (ux, y1 + 18 * SS)], fill=RED, width=4 * SS)

            img.resize((W, H), Image.LANCZOS).save(os.path.join(work, f"f{fi:04d}.png"))

        hold = max(0.0, duration - frames / FPS)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS),
                        "-i", os.path.join(work, "f%04d.png"),
                        "-vf", f"tpad=stop_mode=clone:stop_duration={hold:.3f},format=yuv420p",
                        "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast",
                        "-crf", "18", "-t", f"{duration:.3f}", out_mp4], check=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return out_mp4


TONE_BY_ROLE = {"open": "cold", "build": "warm", "drive": "cold"}
_STOP = {"the", "and", "into", "from", "with", "that", "this", "their",
         "they", "them", "then", "were", "was", "had", "has", "have", "for",
         "its", "his", "her", "a", "an", "of", "to", "in", "on", "at", "by"}


def _key_words(text):
    return [w for w in re.findall(r"[a-z0-9]+", (text or "").lower())
            if len(w) >= 4 and w not in _STOP]


def step_times(steps, words, duration):
    """When each step's first meaningful word is spoken in this scene,
    matched on 5-letter prefixes so "shelling"/"shells" and
    "escorts"/"escort" meet. Steps
    whose words are not in this scene are spread evenly between their
    neighbours; the result is kept in order and inside the scene."""
    spoken = [(re.sub(r"[^a-z0-9]", "", (w.get("word") or "").lower()),
               float(w.get("start", 0.0))) for w in (words or [])]
    times = []
    for st in steps:
        hit = None
        for kw in _key_words(st):
            for tok, t in spoken:
                if tok[:5] == kw[:5] and (not times or t > (times[-1] or 0)):
                    hit = t
                    break
            if hit is not None:
                break
        times.append(hit)
    n = len(steps)
    last = max(0.5, duration - 2.2)
    for i in range(n):
        if times[i] is None:
            prev = next((times[j] for j in range(i - 1, -1, -1) if times[j] is not None), 0.3)
            nxt = next((times[j] for j in range(i + 1, n) if times[j] is not None), last)
            gaps = sum(1 for j in range(i, n) if times[j] is None) + 1
            times[i] = prev + (nxt - prev) / gaps
    out, floor = [], 0.3
    for t in times:
        t = min(max(t, floor), last)
        out.append(t)
        floor = t + 0.6
    return out


def render_chain_scene(scene, out_mp4, duration):
    """The chain for one scene, or None."""
    steps = [s for s in (scene.get("chain") or []) if isinstance(s, str) and s.strip()]
    if len(steps) < 3:
        return None
    try:
        times = step_times(steps, scene.get("words") or [], duration)
        bg = (scene.get("image_paths") or [scene.get("image_path")])[0]
        tone = TONE_BY_ROLE.get(scene.get("chapter_role"), "neutral")
        render([{"text": s, "at": t} for s, t in zip(steps, times)], bg,
               out_mp4, duration, tone=tone)
        print(f"⛓️  Chain of consequences: {' → '.join(steps)}.")
        return out_mp4
    except Exception as e:
        print(f"⚠️ Chain skipped ({type(e).__name__}: {e}) — keeping the "
              f"scene's photos.")
        return None
