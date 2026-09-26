"""
Chain of consequences on a timeline: one per long-form video, in the
chapter after the decision.

Approved from the Six-Day War timeline preview. A line draws itself across
the screen; as each step is spoken a red cursor runs along it to that
step's place, the line behind turns red, and a numbered card drops in
(alternating above and below the line) with the step and, when the
narration says it, its date. The newest card is outlined in red, older ones
dim, the last one gets a red underline. Steps are spaced by their real
dates blended with even spacing, so a long wait and a fast collapse read
as such; without dates they are evenly spaced and numbered only.

The steps come from the script step (longform_llm._place_chain), where
every meaningful word of every step must already be in the narration, and
a date only survives if its day and month are spoken there. Background:
the scene's own photo, blurred and darkened in the chapter's tone. Any
failure returns None and the scene keeps its photos.
"""

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


def _back_out(t, c=1.8):
    t = max(0.0, min(1.0, t))
    return 1 + (c + 1) * (t - 1) ** 3 + c * (t - 1) ** 2


def _background(img_path, tone):
    tint, _ = TONES[tone]
    im = Image.open(img_path).convert("RGB")
    r = max(W * SS / im.width, H * SS / im.height)
    im = im.resize((int(im.width * r) + 1, int(im.height * r) + 1))
    x, y = (im.width - W * SS) // 2, (im.height - H * SS) // 2
    im = im.crop((x, y, x + W * SS, y + H * SS)).filter(ImageFilter.GaussianBlur(20 * SS))
    im = ImageEnhance.Color(im).enhance(0.3)
    im = ImageEnhance.Brightness(im).enhance(0.42)
    im = Image.blend(im, Image.new("RGB", im.size, tint), 0.1)
    band = Image.new("L", im.size, 0)
    ImageDraw.Draw(band).rectangle([0, int(H * SS * 0.25), W * SS, int(H * SS * 0.8)], fill=120)
    band = band.filter(ImageFilter.GaussianBlur(60 * SS))
    return Image.composite(Image.new("RGB", im.size, (8, 7, 7)), im, band)


def _positions(days, x0, x1):
    """x of each step: by real date blended with even spacing; a step with
    no date sits between its dated neighbours; no dates at all, even."""
    n = len(days)
    even = [i / (n - 1) for i in range(n)]
    known = [(i, d) for i, d in enumerate(days) if d is not None]
    if len(known) < 2:
        return [x0 + (x1 - x0) * e for e in even]
    d0, d1 = known[0][1], known[-1][1]
    span = max(1, d1 - d0)
    real = [None if d is None else (d - d0) / span for d in days]
    for i in range(n):
        if real[i] is None:
            prev = next(((j, real[j]) for j in range(i - 1, -1, -1) if real[j] is not None), (i - 1, 0.0))
            nxt = next(((j, real[j]) for j in range(i + 1, n) if real[j] is not None), (n, 1.0))
            real[i] = prev[1] + (nxt[1] - prev[1]) * (i - prev[0]) / max(1, nxt[0] - prev[0])
    return [x0 + (x1 - x0) * (0.55 * r + 0.45 * e) for r, e in zip(real, even)]


def _layout(xs, widths, x0, x1, gap):
    """Card centres: on their node when possible, pushed apart so cards on
    the same side never overlap, and kept inside the frame."""
    cx = [min(max(x, x0 + w / 2), x1 - w / 2) for x, w in zip(xs, widths)]
    for side in (0, 1):
        idx = [i for i in range(len(cx)) if i % 2 == side]
        for a, b in zip(idx, idx[1:]):
            need = cx[a] + widths[a] / 2 + gap + widths[b] / 2
            cx[b] = max(cx[b], need)
        for k in range(len(idx) - 1, -1, -1):
            i = idx[k]
            lim = (x1 - widths[i] / 2 if k == len(idx) - 1 else
                   cx[idx[k + 1]] - widths[idx[k + 1]] / 2 - gap - widths[i] / 2)
            cx[i] = min(cx[i], lim)
    return cx


def render(steps, bg_path, out_mp4, duration, tone="cold", year=""):
    """steps: [{"text", "at", "date": "22 MAY" or "", "day": ordinal or None}]"""
    _, ink = TONES[tone]
    dimc = (150, 146, 138)
    bg = _background(bg_path, tone)
    f_date = ImageFont.truetype(MONO, 30 * SS)
    f_num = ImageFont.truetype(MONO, 24 * SS)
    f_lab = ImageFont.truetype(ANTON, 48 * SS)
    f_year = ImageFont.truetype(ANTON, 64 * SS)
    x0, x1, y = 120 * SS, (W - 120) * SS, int(H * SS * 0.52)
    n = len(steps)
    xs = _positions([s.get("day") for s in steps], x0 + 160 * SS, x1 - 160 * SS)
    cw = 390 * SS
    tmp = ImageDraw.Draw(Image.new("L", (1, 1)))
    labels = [wrap(tmp, s["text"].upper(), f_lab, cw - 48 * SS)[:3] for s in steps]
    ch = [70 * SS + 58 * SS * len(l) + 20 * SS for l in labels]
    cx = _layout(xs, [cw] * n, x0, x1, 24 * SS)
    anim_end = min(duration, steps[-1]["at"] + 2.4)
    frames = int(round(anim_end * FPS))
    work = tempfile.mkdtemp(prefix="chain_")
    try:
        for fi in range(frames):
            t = fi / FPS
            z = 1.0 + 0.03 * (t / max(duration, 1))
            bw, bh = int(W * SS / z), int(H * SS / z)
            ox, oy = (W * SS - bw) // 2, (H * SS - bh) // 2
            img = bg.crop((ox, oy, ox + bw, oy + bh)).resize((W * SS, H * SS))
            d = ImageDraw.Draw(img, "RGBA")
            a = ease(t / 0.7)
            if year:
                d.text((x0, 90 * SS), year, font=f_year,
                       fill=RED + (int(230 * ease((t - 0.2) / 0.5)),))
            d.line([(x0, y), (x0 + (x1 - x0) * a, y)], fill=dimc + (200,), width=3 * SS)
            cur = max([i for i, s in enumerate(steps) if t >= s["at"] - 0.35] or [-1])
            if cur >= 0:
                prev_x = xs[cur - 1] if cur > 0 else x0
                k = ease((t - (steps[cur]["at"] - 0.35)) / 0.45)
                cxr = prev_x + (xs[cur] - prev_x) * k
                d.line([(x0, y), (cxr, y)], fill=RED + (255,), width=5 * SS)
                g = 16 * SS
                d.ellipse([cxr - g, y - g, cxr + g, y + g], fill=RED + (70,))
                d.rectangle([cxr - 3 * SS, y - 30 * SS, cxr + 3 * SS, y + 30 * SS], fill=RED + (255,))
            for i, st in enumerate(steps):
                p = t - st["at"]
                if p < 0:
                    if a >= 1:
                        d.line([(xs[i], y - 10 * SS), (xs[i], y + 10 * SS)],
                               fill=dimc + (160,), width=2 * SS)
                    continue
                nxt = steps[i + 1]["at"] if i + 1 < n else None
                dim = 1.0 - 0.45 * ease((t - nxt) / 0.5) if nxt else 1.0
                lit = i == cur
                r = 10 * SS * _back_out(p / 0.35)
                d.ellipse([xs[i] - r, y - r, xs[i] + r, y + r], fill=(RED if lit else ink) + (255,))
                ca = ease((p - 0.1) / 0.4)
                if ca <= 0:
                    continue
                above = i % 2 == 0
                off = (1 - ca) * 26 * SS
                top = (y - 70 * SS - ch[i] - off) if above else (y + 70 * SS + off)
                bx0, bx1 = cx[i] - cw / 2, cx[i] + cw / 2
                sy = top + ch[i] if above else top
                d.line([(xs[i], y), (cx[i], sy)],
                       fill=(RED if lit else dimc) + (int(220 * ca * dim),), width=2 * SS)
                d.rectangle([bx0, top, bx1, top + ch[i]], fill=(0, 0, 0, int(165 * ca)))
                d.rectangle([bx0, top, bx1, top + ch[i]],
                            outline=(RED if lit else ink) + (int(255 * ca * (1 if lit else dim * 0.6)),),
                            width=(3 if lit else 2) * SS)
                d.text((bx0 + 22 * SS, top + 20 * SS), f"{i + 1:02d}", font=f_num,
                       fill=RED + (int(255 * ca),))
                date = st.get("date") or ""
                nd = int(min(len(date), max(0, (p - 0.1) / 0.05)))
                if nd:
                    dw = d.textlength(date, font=f_date)
                    d.text((bx1 - 22 * SS - dw, top + 17 * SS), date[:nd], font=f_date,
                           fill=(RED if lit else ink) + (int(255 * dim),))
                for j, ln in enumerate(labels[i]):
                    lw = d.textlength(ln, font=f_lab)
                    d.text((cx[i] - lw / 2, top + 62 * SS + j * 58 * SS), ln, font=f_lab,
                           fill=ink + (int(255 * ca * dim),))
            ua = ease((t - steps[-1]["at"] - 0.8) / 0.8)
            if ua > 0:
                i = n - 1
                top = (y - 70 * SS - ch[i]) if i % 2 == 0 else (y + 70 * SS)
                yy = top + ch[i] + 12 * SS
                d.line([(cx[i] - cw / 2, yy), (cx[i] - cw / 2 + cw * ua, yy)], fill=RED, width=4 * SS)
            img.resize((W, H), Image.LANCZOS).save(os.path.join(work, f"f{fi:04d}.jpg"), quality=92)

        hold = max(0.0, duration - frames / FPS)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS),
                        "-i", os.path.join(work, "f%04d.jpg"),
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
        dates = (scene.get("chain_dates") or [None] * len(steps))[:len(steps)]
        dates += [None] * (len(steps) - len(dates))
        render([{"text": s, "at": t, "date": (dt or {}).get("label", ""),
                 "day": (dt or {}).get("day")}
                for s, t, dt in zip(steps, times, dates)], bg,
               out_mp4, duration, tone=tone, year=scene.get("chain_year") or "")
        print(f"⛓️  Timeline of consequences: " + " → ".join(
            f"{s} ({dt['label']})" if dt else s for s, dt in zip(steps, dates)) + ".")
        return out_mp4
    except Exception as e:
        print(f"⚠️ Chain skipped ({type(e).__name__}: {e}) — keeping the "
              f"scene's photos.")
        return None
