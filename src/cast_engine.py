"""
Cast cards: a freeze-frame introduction for each main person of the story.

On the spoken name the picture freezes, everything but the person drops
into a dark red duotone, the person pops forward with a pale cut-out outline
and faint speed lines behind, and the name slams in beside them with a role
stamp ("PRESIDENT OF EGYPT", or "DECISION MAKER" for the one who decided)
and the Wikipedia description.

FOOTAGE WHEN POSSIBLE, PHOTO OTHERWISE
The person's real footage is searched on Wikimedia Commons (same licence
bar as every other clip). A frame is only used when:
  - one face fills it (any other face is far smaller),
  - that face matches the person's Wikipedia portrait (SFace cosine at or
    above MATCH; on the Six-Day War test Nasser's own frames scored
    0.55-0.64 and three other leaders' portraits never passed 0.27),
  - the eyes are open (the first test froze Nasser mid-blink),
  - the cut-out is clean (FUZZY_MAX).
The footage plays up to that frame and freezes on the name. Without such a
frame the portrait itself is cut out; if even that is not clean (a tiny or
blurred photo), the caller falls back to the plain case-file card.

Models (models/cast, fetched by the workflows): u2net_human_seg for the
cut-out, YuNet for faces, SFace for matching. Any missing model only
removes the step that needs it.
"""

import glob
import math
import os
import random
import re
import shutil
import subprocess
import tempfile

import numpy as np
from PIL import (Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter,
                 ImageFont, ImageOps)

W, H, FPS = 1920, 1080, 30
BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
ANTON = os.path.join(BASE, "fonts", "Anton-Regular.ttf")
MONO = os.path.join(BASE, "fonts", "FreeMonoBold.ttf")
MODELS = os.path.join(BASE, "models", "cast")
SEG_MODEL = os.path.join(MODELS, "u2net_human_seg.onnx")
DET_MODEL = os.path.join(MODELS, "face_detection_yunet_2023mar.onnx")
REC_MODEL = os.path.join(MODELS, "face_recognition_sface_2021dec.onnx")
RED = (205, 58, 46)
INK = (240, 236, 226)
DIM = (170, 166, 156)

MATCH = 0.42            # SFace cosine; OpenCV's own "same person" line is 0.363
MIN_FACE = 0.16         # face height as a share of the frame
FUZZY_MAX = 0.08        # soft-edge share of the cut-out; clean ones ~0.015
MIN_PHOTO_HEIGHT = 400
MAX_CLIPS = 3
MAX_CLIP_MB = 150
SCAN_SECONDS = 600
SCAN_STEP = 0.25


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

_seg = _det = _rec = None


def _seg_session():
    global _seg
    if _seg is None:
        import onnxruntime as ort
        _seg = ort.InferenceSession(SEG_MODEL, providers=["CPUExecutionProvider"])
    return _seg


def _face_models():
    global _det, _rec
    if _det is None:
        import cv2
        _det = cv2.FaceDetectorYN.create(DET_MODEL, "", (320, 320), 0.8, 0.3, 5000)
        _rec = cv2.FaceRecognizerSF.create(REC_MODEL, "")
    return _det, _rec


def cutout_available():
    return os.path.exists(SEG_MODEL)


def faces_available():
    try:
        import cv2  # noqa: F401
    except Exception:
        return False
    return os.path.exists(DET_MODEL) and os.path.exists(REC_MODEL)


def person_mask(img):
    """Soft person mask (L) the size of img."""
    s = _seg_session()
    x = np.asarray(img.convert("RGB").resize((320, 320), Image.LANCZOS), np.float32)
    x = x / max(float(x.max()), 1e-6)
    x = (x - [0.485, 0.456, 0.406]) / [0.229, 0.224, 0.225]
    x = x.transpose(2, 0, 1)[None].astype(np.float32)
    out = s.run(None, {s.get_inputs()[0].name: x})[0][0, 0]
    out = (out - out.min()) / max(float(out.max() - out.min()), 1e-6)
    return Image.fromarray((out * 255).astype(np.uint8)).resize(img.size, Image.LANCZOS)


def _mask_quality(mask):
    a = np.asarray(mask, np.float32) / 255
    area = float((a > 0.5).mean())
    fuzzy = float(((a > 0.15) & (a < 0.85)).mean()) / max(area, 1e-6)
    return area, fuzzy


def _mask_ok(mask):
    """Clean edge, sensible size, and no slab of background: a person's
    upper half never runs along the picture's side edges (the first footage
    test took a strip of curtain next to Nasser as part of him)."""
    area, fuzzy = _mask_quality(mask)
    a = np.asarray(mask, np.float32) / 255 > 0.5
    h, w = a.shape
    top = a[: h // 2]
    band = max(2, int(w * 0.03))
    edge = max(top[:, :band].mean(), top[:, -band:].mean())
    ok = 0.06 < area < 0.92 and fuzzy <= FUZZY_MAX and edge < 0.3
    return ok, f"area {area:.2f}, soft edge {fuzzy:.2f}, side contact {edge:.2f}"


def _faces(bgr):
    det, _ = _face_models()
    h, w = bgr.shape[:2]
    det.setInputSize((w, h))
    _, f = det.detect(bgr)
    return [] if f is None else sorted(f, key=lambda x: -x[2] * x[3])


def _embed(bgr, face):
    _, rec = _face_models()
    return rec.feature(rec.alignCrop(bgr, face))


def _similarity(a, b):
    import cv2
    _, rec = _face_models()
    return float(rec.match(a, b, cv2.FaceRecognizerSF_FR_COSINE))


def face_reference(portrait_path):
    import cv2
    img = cv2.imread(portrait_path)
    if img is None:
        return None
    s = 1200 / max(img.shape[:2])
    if s < 1:
        img = cv2.resize(img, None, fx=s, fy=s)
    f = _faces(img)
    return _embed(img, f[0]) if f else None


# ---------------------------------------------------------------------------
# Footage: find the frame
# ---------------------------------------------------------------------------

def _scan(video_path, ref, min_t):
    """Frames where the person is alone, large and recognised."""
    import cv2
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    n = int(min(cap.get(cv2.CAP_PROP_FRAME_COUNT), fps * SCAN_SECONDS))
    out = []
    for i in range(int(min_t * fps), n, max(1, int(fps * SCAN_STEP))):
        cap.set(cv2.CAP_PROP_POS_FRAMES, i)
        ok, img = cap.read()
        if not ok:
            break
        h = img.shape[0]
        f = _faces(img)
        if not f:
            continue
        big = f[0]
        if big[3] < MIN_FACE * h or big[14] < 0.9:
            continue
        if len(f) > 1 and f[1][2] * f[1][3] > 0.15 * big[2] * big[3]:
            continue
        sim = _similarity(ref, _embed(img, big))
        if sim < MATCH:
            continue
        # open eyes: the eye patches of an open eye carry the contrast of
        # iris against white; a closed lid is flat
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(float)
        r = max(3, int(big[2] * 0.09))
        eyes = []
        for ex, ey in ((big[4], big[5]), (big[6], big[7])):
            p = g[max(0, int(ey) - r):int(ey) + r, max(0, int(ex) - r):int(ex) + r]
            eyes.append(p.std() if p.size else 0.0)
        out.append({"t": i / fps, "sim": sim, "eyes": min(eyes),
                    "x": float((big[0] + big[2] / 2) / img.shape[1])})
    cap.release()
    return out


def _content_box(img, face_x=None):
    """The picture inside any black bars, cropped to a portrait shape
    around the face (face_x: 0-1 across the frame)."""
    a = np.asarray(img.convert("L")).astype(float)
    cols = np.where(a.mean(0) > 12)[0]
    rows = np.where(a.mean(1) > 12)[0]
    if not len(cols) or not len(rows):
        x0, y0, x1, y1 = 0, 0, img.width, img.height
    else:
        x0, y0, x1, y1 = int(cols[0]), int(rows[0]), int(cols[-1]) + 1, int(rows[-1]) + 1
    # TV footage carries channel logos and burned-in captions in its
    # corners: a portrait-shaped crop around the face drops the sides, and a
    # thin trim top and bottom takes the rest. It also keeps a wide frame
    # out of the text column.
    my = int((y1 - y0) * 0.05)
    y0, y1 = y0 + my, y1 - my
    cw = min(x1 - x0, int((y1 - y0) * 0.8))
    cx = (x0 + (face_x if face_x is not None else 0.5) * (x1 - x0))
    left = int(min(max(cx - cw / 2, x0), x1 - cw))
    return (left, y0, left + cw, y1)


def _grab(video_path, t):
    fd, png = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t:.3f}",
                        "-i", video_path, "-frames:v", "1", png], check=True)
        return Image.open(png).convert("RGB")
    finally:
        os.remove(png)


def find_footage(name, portrait, era, work_dir, lead_seconds):
    """{"frames": [PIL...], "credit"} — footage of `name` ending on a frame
    that passes every check — or None."""
    if not (faces_available() and cutout_available()):
        return None
    from archive_engine import (search_commons_video, _polite_get, HEADERS,
                                format_credit)
    ref = face_reference(portrait)
    if ref is None:
        print(f"   ↩️  {name}: no face found in the portrait to match footage against.")
        return None
    # No title filter: many of these files are titled in Arabic, Hebrew or
    # Russian. The face match below is what decides who is on screen.
    clips = search_commons_video(name, era=era)[:MAX_CLIPS]
    for k, c in enumerate(clips):
        path = os.path.join(work_dir, f"cast_src_{k}.webm")
        try:
            with _polite_get(c["url"], stream=True, timeout=120,
                             headers={**HEADERS, "Accept": "*/*"}) as r:
                r.raise_for_status()
                size = 0
                with open(path, "wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        size += len(chunk)
                        if size > MAX_CLIP_MB * 1024 * 1024:
                            raise ValueError("clip too large")
                        f.write(chunk)
            hits = _scan(path, ref, min_t=lead_seconds)
            if not hits:
                print(f"   ↩️  {name}: no clear, matching frame in {c['title']!r}.")
                continue
            top_eyes = max(h["eyes"] for h in hits)
            ranked = sorted((h for h in hits if h["eyes"] >= 0.8 * top_eyes),
                            key=lambda h: -h["sim"])
            for h in ranked[:5]:
                freeze = _grab(path, h["t"])
                box = _content_box(freeze, h["x"])
                ok, why = _mask_ok(person_mask(_prepare(freeze.crop(box), True)))
                if not ok:
                    continue
                lead_dir = tempfile.mkdtemp(prefix="lead_", dir=work_dir)
                start = max(0.0, h["t"] - lead_seconds)
                subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{start:.3f}",
                                "-i", path, "-t", f"{h['t'] - start:.3f}", "-vf", "fps=30",
                                os.path.join(lead_dir, "%04d.png")], check=True)
                frames = [Image.open(f).convert("RGB").crop(box)
                          for f in sorted(glob.glob(os.path.join(lead_dir, "*.png")))]
                shutil.rmtree(lead_dir, ignore_errors=True)
                frames.append(freeze.crop(box))
                print(f"   🎞️  {name}: footage frame at {h['t']:.1f}s of {c['title']!r} "
                      f"(face match {h['sim']:.2f}).")
                return {"frames": frames, "credit": format_credit(c)}
            print(f"   ↩️  {name}: matching frames in {c['title']!r} did not cut out cleanly.")
        except Exception as e:
            print(f"   ↩️  {name}: footage {c.get('title')!r} unusable ({type(e).__name__}: {e}).")
        finally:
            if os.path.exists(path):
                os.remove(path)
    return None


# ---------------------------------------------------------------------------
# The card
# ---------------------------------------------------------------------------

def _ease(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


def _back_out(t, c=1.7):
    t = max(0.0, min(1.0, t))
    return 1 + (c + 1) * (t - 1) ** 3 + c * (t - 1) ** 2


def _duotone(img, dark=(10, 8, 8), light=(150, 44, 36)):
    g = ImageOps.autocontrast(ImageOps.grayscale(img), cutoff=1)
    return ImageOps.colorize(g, dark, light)


def _prepare(src, footage):
    """The picture at the size it takes on the card (the frame's full
    height), footage lightly sharpened against the upscale. The cut-out is
    judged on exactly this image, both when footage is chosen and when the
    card is drawn."""
    ph = ImageOps.contain(src.convert("RGB"), (int(W * 0.62), int(H * 1.02)))
    s = H * 1.02 / ph.height
    ph = ph.resize((int(ph.width * s), int(ph.height * s)), Image.LANCZOS)
    if footage:
        ph = ph.filter(ImageFilter.UnsharpMask(2, 60, 2))
    return ph


def _layers(src, side, footage):
    """(plain, frozen_bg, person, outline, glow, centre, geometry). The
    person keeps the exact place they had in the plain frame, so the freeze
    reads as the same picture changing."""
    ph = _prepare(src, footage)
    px = int(W * (0.30 if side == "left" else 0.70) - ph.width / 2)
    py = H - ph.height
    mask = person_mask(ph)
    ok, why = _mask_ok(mask)
    if not ok:
        raise ValueError(f"no clean cut-out ({why})")
    a = np.clip((np.asarray(mask, np.float32) / 255 - 0.35) / 0.3, 0, 1)
    mask = Image.fromarray((a * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.2))

    backdrop = ImageOps.fit(ph, (W, H)).filter(ImageFilter.GaussianBlur(28))
    backdrop = ImageEnhance.Brightness(backdrop).enhance(0.45)
    plain = backdrop.copy()
    plain.paste(ph, (px, py))
    # frozen: only the blurred backdrop remains behind the person, so the
    # picture's own rectangle disappears on the freeze
    frozen_bg = ImageEnhance.Brightness(_duotone(backdrop)).enhance(0.5)

    full = Image.new("L", (W, H), 0)
    full.paste(mask, (px, py))
    person = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    person.paste(ph, (px, py))
    person.putalpha(full)
    grown = full.filter(ImageFilter.MaxFilter(15))
    outline = Image.new("RGBA", (W, H), INK + (0,))
    outline.putalpha(ImageChops.subtract(grown, full))
    glow = Image.new("RGBA", (W, H), RED + (0,))
    glow.putalpha(grown.filter(ImageFilter.MaxFilter(21))
                  .filter(ImageFilter.GaussianBlur(26)).point(lambda v: int(v * 0.8)))
    bbox = full.getbbox() or (0, 0, W, H)
    centre = ((bbox[0] + bbox[2]) / 2, bbox[1] + (bbox[3] - bbox[1]) * 0.3)
    return plain, frozen_bg, person, outline, glow, centre, {"size": ph.size, "pos": (px, py)}


def _speed_lines(centre, seed, side):
    rnd = random.Random(seed)
    im = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(im)
    cx, cy = centre
    for _ in range(90):
        ang = rnd.uniform(0, 2 * math.pi)
        r0 = rnd.uniform(380, 520)
        r1 = r0 + rnd.uniform(500, 1400)
        wd = rnd.uniform(0.004, 0.012)
        d.polygon([(cx + r0 * math.cos(ang), cy + r0 * math.sin(ang)),
                   (cx + r1 * math.cos(ang - wd), cy + r1 * math.sin(ang - wd)),
                   (cx + r1 * math.cos(ang + wd), cy + r1 * math.sin(ang + wd))],
                  fill=rnd.randint(40, 90))
    # the text column stays clean: the lines fade out where the name sits
    x = np.linspace(0, 1, W)
    fade = (np.clip((0.52 - x) / 0.08, 0, 1) if side == "left"
            else np.clip((x - 0.48) / 0.08, 0, 1))
    keep = Image.fromarray((np.tile(fade, (H, 1)) * 255).astype(np.uint8))
    return ImageChops.multiply(im, keep)


def _stamp_tile(text):
    f = ImageFont.truetype(MONO, 40)
    tw = int(ImageDraw.Draw(Image.new("L", (1, 1))).textlength(text, font=f))
    im = Image.new("RGBA", (tw + 48, 80), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rectangle([3, 3, im.width - 4, im.height - 4], outline=RED + (240,), width=5)
    d.text((24, 17), text, font=f, fill=RED + (240,))
    rnd = random.Random(11)
    p = im.load()
    for _ in range(im.width * im.height // 14):
        x, y = rnd.randrange(im.width), rnd.randrange(im.height)
        r, g, b, a = p[x, y]
        p[x, y] = (r, g, b, int(a * 0.25))
    return im.rotate(-4, expand=True, resample=Image.BICUBIC)


def _lead_frame(fr, geo):
    """A footage frame before the freeze, in the freeze frame's exact place,
    over its own blurred copy, lightly sharpened against the upscale."""
    fr = fr.convert("RGB").resize(geo["size"], Image.LANCZOS)
    fr = fr.filter(ImageFilter.UnsharpMask(2, 60, 2))
    bd = ImageOps.fit(fr, (W // 8, H // 8)).filter(ImageFilter.GaussianBlur(3)).resize((W, H))
    bd = ImageEnhance.Brightness(bd).enhance(0.45)
    bd.paste(fr, geo["pos"])
    return bd


def render_card(name, role, lines, src, out_mp4, duration, freeze_at,
                side="left", file_no=1, lead=None):
    """src: the portrait (PIL) or, with lead, the footage's freeze frame;
    lead: the footage frames before it, played until freeze_at."""
    plain, frozen_bg, person, outline, glow, centre, geo = _layers(src, side, bool(lead))
    streak_mask = _speed_lines(centre, file_no, side)
    stamp = _stamp_tile(short_role(role))
    words = name.upper().split()
    top, last = " ".join(words[:-1]), words[-1]
    f_top = ImageFont.truetype(ANTON, 70)
    f_big = ImageFont.truetype(ANTON, 190 if len(last) <= 8 else 150)
    f_small = ImageFont.truetype(MONO, 32)
    f_tiny = ImageFont.truetype(MONO, 26)
    tx = int(W * 0.56) if side == "left" else 120
    tf = max(0.5, freeze_at)
    type_len = sum(len(x) for x in lines) * 0.03
    anim_end = min(duration, tf + 0.75 + type_len + 0.6)
    frames = int(round(anim_end * FPS))
    work = tempfile.mkdtemp(prefix="cast_")
    try:
        for fi in range(frames):
            t = fi / FPS
            k = _ease((t - tf) / 0.22)
            pop = _back_out((t - tf) / 0.35) if t >= tf else 0.0
            before = bool(lead) and t < tf
            if before:
                j = min(len(lead) - 1, int(round((t / tf) * (len(lead) - 1))))
                base = _lead_frame(lead[j], geo)
            else:
                base = plain
            img = Image.blend(base, frozen_bg, k).convert("RGBA")
            if k > 0:
                sl = streak_mask.rotate(-(t - tf) * 1.5, center=centre)
                streaks = Image.new("RGBA", (W, H), (230, 210, 195, 0))
                streaks.putalpha(sl.point(lambda v: int(v * k)))
                img.alpha_composite(streaks)
                g = glow.copy()
                g.putalpha(g.getchannel("A").point(lambda v: int(v * k)))
                img.alpha_composite(g)
            if not before:
                layer = person.copy()
                if k > 0:
                    o = outline.copy()
                    o.putalpha(o.getchannel("A").point(lambda v: int(v * k)))
                    layer.alpha_composite(o)
                sc = 1.0 + 0.045 * pop
                if sc > 1.0001:
                    big = layer.resize((int(W * sc), int(H * sc)), Image.BICUBIC)
                    ox, oy = int(centre[0] * sc - centre[0]), int(centre[1] * sc - centre[1])
                    layer = big.crop((ox, oy, ox + W, oy + H))
                img.alpha_composite(layer)
            fl = 1 - (t - tf) / 0.12
            if 0 < fl <= 1:
                img = Image.blend(img, Image.new("RGBA", (W, H), (255, 250, 240, 255)), 0.35 * fl)

            d = ImageDraw.Draw(img)
            if t >= tf + 0.1:
                d.text((tx, 250), f"FILE {file_no:02d}", font=f_tiny, fill=RED)
            a1 = _ease((t - tf - 0.12) / 0.22)
            if a1 > 0 and top:
                d.text((tx + (1 - a1) * 60, 300), top, font=f_top, fill=INK + (int(255 * a1),))
            a2 = (t - tf - 0.2) / 0.18
            if a2 > 0:
                s2 = 1 + 0.35 * (1 - _back_out(a2, 2.2))
                tile = Image.new("RGBA", (int(d.textlength(last, font=f_big)) + 20, 240), (0, 0, 0, 0))
                ImageDraw.Draw(tile).text((0, 0), last, font=f_big,
                                          fill=INK + (int(255 * min(1, a2 * 3)),))
                if s2 > 1.001:
                    tile = tile.resize((int(tile.width * s2), int(tile.height * s2)), Image.BICUBIC)
                img.alpha_composite(tile, (tx, int(385 - (tile.height - 240) / 2)))
            s = (t - tf - 0.45) / 0.26
            if s > 0:
                z = 1.5 - 0.5 * min(1, _back_out(s))
                st = stamp.resize((int(stamp.width * z), int(stamp.height * z)))
                st.putalpha(st.getchannel("A").point(lambda v: int(v * min(1, s * 3))))
                img.alpha_composite(st, (tx - 6, 690 - st.height // 2))
            if t >= tf + 0.75:
                budget = (t - tf - 0.75) / 0.03
                yy = 770
                for j, ln in enumerate(lines):
                    n = int(min(len(ln), max(0, budget)))
                    if n:
                        d.text((tx, yy), ln[:n], font=f_small, fill=INK if j == 0 else DIM)
                    budget -= len(ln)
                    yy += 48
            img.convert("RGB").save(os.path.join(work, f"f{fi:04d}.jpg"), quality=92)
        hold = max(0.0, duration - frames / FPS)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS),
                        "-i", os.path.join(work, "f%04d.jpg"),
                        "-vf", f"tpad=stop_mode=clone:stop_duration={hold:.3f},"
                               "zoompan=z='min(1+0.0003*on,1.035)':d=1:s=1920x1080:fps=30,"
                               "format=yuv420p",
                        "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast",
                        "-crf", "18", "-t", f"{duration:.3f}", out_mp4], check=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return out_mp4


def short_role(role, limit=26):
    """A role that fits the stamp whole: "Chief of Staff of the Israel
    Defense Forces" becomes "CHIEF OF STAFF", never "...THE ISRAEL D"."""
    r = " ".join((role or "").split()).upper()
    if len(r) <= limit:
        return r
    for sep in (" OF THE ", ", ", " OF "):
        head = r.split(sep)[0]
        if 3 <= len(head) <= limit:
            return head
    cut = r[:limit].rsplit(" ", 1)[0]
    return cut if len(cut) >= 3 else r[:limit]


def card_lines(role, info_lines):
    """The lines under the stamp: the Wikipedia description, minus what the
    stamp already says ("PRESIDENT OF EGYPT FROM 1956 TO 1970" under a
    PRESIDENT OF EGYPT stamp becomes "IN OFFICE 1956 – 1970")."""
    lines = list(info_lines or [])
    if lines and role and role.upper() in lines[0]:
        yrs = re.search(r"FROM (\d{4}) TO (\d{4})", lines[0])
        lines = ([f"IN OFFICE {yrs.group(1)} – {yrs.group(2)}"] if yrs else []) + lines[1:]
    return [x[:44] for x in lines][:2]


def render_cast_scene(scene, out_mp4, duration, build_dir, info, freeze_at, era=None):
    """The cast card for one scene: footage when a frame passes every check,
    the portrait otherwise. Returns (path, credit) or None (the caller then
    uses the plain case-file card)."""
    if not cutout_available():
        return None
    d = scene.get("dossier") or {}
    name = d["name"]
    shown = d.get("shown") or name
    role = "DECISION MAKER" if d.get("decision") else (d.get("role") or "")
    if not role:
        role = (info.get("lines") or [""])[0][:30]
    lines = card_lines(d.get("role") or role, info.get("lines"))
    side = d.get("side") or "left"
    file_no = d.get("file_no") or 1
    work = tempfile.mkdtemp(prefix="cast_", dir=build_dir)
    try:
        foot = None
        try:
            foot = find_footage(name, info["photo"], era, work, max(0.5, freeze_at))
        except Exception as e:
            print(f"   ↩️  {name}: footage search failed ({type(e).__name__}: {e}).")
        if foot:
            try:
                render_card(shown, role, lines, foot["frames"][-1], out_mp4, duration,
                            freeze_at, side, file_no, lead=foot["frames"][:-1])
                return out_mp4, foot["credit"], "footage"
            except Exception as e:
                print(f"   ↩️  {name}: footage card failed ({type(e).__name__}: {e}).")
        photo = Image.open(info["photo"]).convert("RGB")
        if photo.height < MIN_PHOTO_HEIGHT:
            print(f"   ↩️  {name}: portrait too small for a clean cut-out.")
            return None
        render_card(shown, role, lines, photo, out_mp4, duration, freeze_at, side, file_no)
        return out_mp4, info["credit"], "photo"
    except Exception as e:
        print(f"   ↩️  {name}: cast card failed ({type(e).__name__}: {e}).")
        return None
    finally:
        shutil.rmtree(work, ignore_errors=True)
