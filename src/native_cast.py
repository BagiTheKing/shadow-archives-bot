"""
The native Short's person introduction, 1080x1920: the long video's cast
card (cast_engine) rebuilt for a vertical screen and a viewer one swipe
from leaving (owner, 25 Oct: "when we present anyone, the presentation will
be hyped with animations ... so that everything is moving").

Like a fighting-game character select: the person's real footage plays (or
their photo pushes in fast) until the narrator says the name; on the name
the frame freezes with a flash and a jolt, everything but the person drops
into a dark red duotone, a red slash cuts behind them, speed lines spin,
the person pops forward with a pale outline, and the name slams in above
their head under the role stamp. Nothing stops moving until the
next cut: the person keeps pushing in, the lines keep turning, the slash
keeps sliding.

The layout keeps clear of the promise line at the top (to ~y 500) and the
captions (~y 1440): the name sits between them, the person below it.
"""

import math
import os
import random
import shutil
import subprocess
import tempfile

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

import cast_engine as ce

W, H, FPS = 1080, 1920, 30
RED, INK, DIM = ce.RED, ce.INK, ce.DIM
NAME_Y = 520                     # top of the name block (role, first name, surname)
FACE_TOP, FACE_H = 900, 400      # where the face sits, under the name
FACE_MAX = 520                   # the largest a face may grow to fill the width


def dossier_for(subject, script):
    """The long-form script's cast entry for this person ({"name", "shown",
    "role", "decision", "file_no"}), matched on the surname, or a bare one."""
    words = [w for w in (subject or "").split() if not w.isdigit()]
    name = " ".join(words)
    last = (words[-1] if words else "").lower()
    for c in script.get("chapters") or []:
        for s in c.get("scenes") or []:
            d = s.get("dossier") or {}
            if last and last in (d.get("name") or "").lower():
                return d
    return {"name": name, "shown": name, "role": "", "decision": False, "file_no": 1}


def name_time(words, name):
    """When the narrator starts saying the surname (seconds into the beat),
    or None."""
    last = (name.split() or [""])[-1].lower().strip(".,")
    for w in words or []:
        if w.get("word", "").lower().strip(".,'’s") .startswith(last[:5]):
            return float(w["start"])
    return None


# ---------------------------------------------------------------------------
# Layers
# ---------------------------------------------------------------------------

def _geometry(src):
    """(scale, x, y) that put the person's face FACE_H tall with its top at
    FACE_TOP, centred, the picture reaching the bottom edge: the head sits
    between the name above and the captions below."""
    sw, sh = src.size
    face = None
    if ce.faces_available():
        import cv2
        bgr = cv2.cvtColor(np.asarray(src.convert("RGB")), cv2.COLOR_RGB2BGR)
        found = ce._faces(bgr)
        if found:
            face = found[0][:4]
    if face is None:
        s = (H - FACE_TOP + 60) / sh
        return s, (W - sw * s) / 2, H - sh * s
    fx, fy, fw, fh = (float(v) for v in face)
    # the photo fills the width where it can without the face outgrowing
    # the space between the name and the captions, and reaches the bottom
    s = max(FACE_H / fh, min(W / sw, FACE_MAX / fh))
    s = max(s, (H - FACE_TOP) / max(1.0, sh - fy))
    return s, W / 2 - (fx + fw / 2) * s, FACE_TOP - fy * s


def _place(img, geo):
    """img scaled and placed by geo: (the part inside the frame, its x, y)."""
    s, x, y = geo
    big = img.convert("RGB").resize((max(1, int(img.width * s)), max(1, int(img.height * s))),
                                    Image.LANCZOS)
    x0, y0 = max(0, int(-x)), max(0, int(-y))
    x1, y1 = min(big.width, int(W - x)), min(big.height, int(H - y))
    return big.crop((x0, y0, x1, y1)), (max(0, int(x)), max(0, int(y)))


def _layers(src):
    geo = _geometry(src)
    ph, (px, py) = _place(src, geo)
    mask = ce.person_mask(ph)
    # a tight portrait's shoulders touch the frame's sides, which the long
    # card's slab-of-background check would refuse: here only a clean edge
    # and a sensible size count
    area, fuzzy = ce._mask_quality(mask)
    area *= ph.width * ph.height / (W * H)            # as a share of the screen
    if not (0.04 < area < 0.75 and fuzzy <= ce.FUZZY_MAX):
        raise ValueError(f"no clean cut-out (area {area:.2f}, soft edge {fuzzy:.2f})")
    a = np.clip((np.asarray(mask, np.float32) / 255 - 0.35) / 0.3, 0, 1)
    mask = Image.fromarray((a * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.2))
    backdrop = ImageOps.fit(ph, (W, H)).filter(ImageFilter.GaussianBlur(30))
    backdrop = ImageEnhance.Brightness(backdrop).enhance(0.5)
    plain = backdrop.copy()
    plain.paste(ph, (px, py))
    frozen_bg = ImageEnhance.Brightness(ce._duotone(backdrop)).enhance(0.55)
    full = Image.new("L", (W, H), 0)
    full.paste(mask, (px, py))
    person = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    person.paste(ph, (px, py))
    person.putalpha(full)
    grown = full.filter(ImageFilter.MaxFilter(13))
    outline = Image.new("RGBA", (W, H), INK + (0,))
    outline.putalpha(ImageChops.subtract(grown, full))
    glow = Image.new("RGBA", (W, H), RED + (0,))
    glow.putalpha(grown.filter(ImageFilter.MaxFilter(21))
                  .filter(ImageFilter.GaussianBlur(30)).point(lambda v: int(v * 0.85)))
    box = full.getbbox() or (0, py, W, H)
    centre = ((box[0] + box[2]) / 2, box[1] + (box[3] - box[1]) * 0.25)
    return plain, frozen_bg, person, outline, glow, centre, geo


def _speed_lines(centre, seed):
    rnd = random.Random(seed)
    im = Image.new("L", (W * 2, H * 2), 0)
    d = ImageDraw.Draw(im)
    cx, cy = W, H                     # drawn around the middle, then shifted
    for _ in range(110):
        ang = rnd.uniform(0, 2 * math.pi)
        r0 = rnd.uniform(330, 470)
        r1 = r0 + rnd.uniform(700, 1700)
        wd = rnd.uniform(0.004, 0.013)
        d.polygon([(cx + r0 * math.cos(ang), cy + r0 * math.sin(ang)),
                   (cx + r1 * math.cos(ang - wd), cy + r1 * math.sin(ang - wd)),
                   (cx + r1 * math.cos(ang + wd), cy + r1 * math.sin(ang + wd))],
                  fill=rnd.randint(45, 95))
    return im, (int(W - centre[0]), int(H - centre[1]))


def _slash():
    """A red band cutting behind the person at an angle, ragged-edged."""
    im = Image.new("RGBA", (W * 2, 420), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    for y in range(60, 360):                       # darker towards its edges
        a = 1 - abs(y - 210) / 150
        d.line([(0, y), (im.width, y)], fill=RED + (int(120 + 110 * a),))
    d.rectangle([0, 30, im.width, 42], fill=INK + (170,))
    d.rectangle([0, 378, im.width, 384], fill=INK + (120,))
    return im.rotate(14, expand=True, resample=Image.BICUBIC)


def _lead_frame(fr, geo):
    """A footage frame before the freeze, in the freeze frame's exact place."""
    part, pos = _place(fr, geo)
    part = part.filter(ImageFilter.UnsharpMask(2, 60, 2))
    bd = ImageOps.fit(part, (W // 8, H // 8)).filter(ImageFilter.GaussianBlur(3)).resize((W, H))
    bd = ImageEnhance.Brightness(bd).enhance(0.5)
    bd.paste(part, pos)
    return bd


def _zoom(img, z, centre):
    """img scaled by z around centre, same size."""
    if abs(z - 1) < 1e-4:
        return img
    big = img.resize((int(img.width * z), int(img.height * z)), Image.BICUBIC)
    ox, oy = int(centre[0] * z - centre[0]), int(centre[1] * z - centre[1])
    return big.crop((ox, oy, ox + img.width, oy + img.height))


# ---------------------------------------------------------------------------
# The card
# ---------------------------------------------------------------------------

def render(name, role, src, out_mp4, duration, freeze_at, lead=None, seed=1):
    """src: the photo (PIL) or, with lead, the footage's freeze frame;
    lead: the footage frames before it, played until freeze_at."""
    plain, frozen_bg, person, outline, glow, centre, geo = _layers(src)
    full_photo = ImageOps.fit(src.convert("RGB"), (W, H), Image.LANCZOS, centering=(0.5, 0.35))
    lines, lines_off = _speed_lines(centre, seed)
    slash = _slash()
    stamp = ce._stamp_tile(ce.short_role(role, 22)) if role else None
    words = name.upper().split()
    top, last = " ".join(words[:-1]), words[-1]
    probe = ImageDraw.Draw(Image.new("L", (1, 1)))
    size_big = 200
    while size_big > 120 and probe.textlength(last, font=ImageFont.truetype(ce.ANTON, size_big)) > W - 90:
        size_big -= 10
    f_big = ImageFont.truetype(ce.ANTON, size_big)
    f_top = ImageFont.truetype(ce.ANTON, 64)
    last_w = probe.textlength(last, font=f_big)
    top_w = probe.textlength(top, font=f_top) if top else 0
    tf = max(0.35, min(freeze_at, duration - 1.2))
    frames = max(1, round(duration * FPS))
    rnd = random.Random(seed)
    work = tempfile.mkdtemp(prefix="ncast_")
    try:
        for fi in range(frames):
            t = fi / FPS
            # footage eases into the freeze; a photo snaps under the flash
            # (easing out of the small framed copy showed it for a beat)
            k = ce._ease((t - tf) / 0.18) if lead or t < tf else 1.0
            pop = ce._back_out((t - tf) / 0.3) if t >= tf else 0.0
            after = max(0.0, t - tf)
            if t < tf:
                if lead:
                    j = min(len(lead) - 1, int(round((t / tf) * (len(lead) - 1))))
                    base = _lead_frame(lead[j], geo)
                else:
                    # the photo full screen, rushing in until the freeze
                    # snaps it into the card (a small framed photo in the
                    # dark read as dead air, 25 Oct)
                    base = _zoom(full_photo, 1.0 + 0.12 * (t / tf) ** 2, (W / 2, H * 0.4))
                img = base.convert("RGBA")
            else:
                img = Image.blend(plain, frozen_bg, k).convert("RGBA")
                # the slash slides through behind the person, never stops
                sx = int(-W * 0.5 + (W * 0.15) * after - W * 0.35 * (1 - ce._ease(after / 0.25)))
                sl = slash.copy()
                sl.putalpha(sl.getchannel("A").point(lambda v: int(v * k)))
                img.alpha_composite(sl, (sx, int(centre[1] + 40 - sl.height / 2)))
                rot = lines.rotate(-after * 6, center=(W, H))
                crop = rot.crop((lines_off[0], lines_off[1], lines_off[0] + W, lines_off[1] + H))
                streaks = Image.new("RGBA", (W, H), (235, 215, 200, 0))
                streaks.putalpha(crop.point(lambda v: int(v * k)))
                img.alpha_composite(streaks)
                g = glow.copy()
                g.putalpha(g.getchannel("A").point(lambda v: int(v * k)))
                img.alpha_composite(g)
                layer = person.copy()
                o = outline.copy()
                o.putalpha(o.getchannel("A").point(lambda v: int(v * k)))
                layer.alpha_composite(o)
                # pop forward, then keep pushing in until the cut
                img.alpha_composite(_zoom(layer, 1.0 + 0.05 * pop + 0.018 * after, centre))
            fl = 1 - (t - tf) / 0.14
            if 0 < fl <= 1:
                img = Image.blend(img, Image.new("RGBA", (W, H), (255, 250, 240, 255)), 0.55 * fl)

            # the name: first name slides in, surname slams down, stamp thunks
            d = ImageDraw.Draw(img)
            a1 = ce._ease((t - tf - 0.08) / 0.2)
            if a1 > 0 and top:
                d.text(((W - top_w) / 2 + (1 - a1) * 80, NAME_Y + 80), top, font=f_top,
                       fill=INK + (int(255 * a1),))
            a2 = (t - tf - 0.16) / 0.16
            slam = 0.0
            if a2 > 0:
                s2 = 1 + 0.6 * (1 - ce._back_out(a2, 2.0))
                tile = Image.new("RGBA", (int(last_w) + 40, size_big + 60), (0, 0, 0, 0))
                td = ImageDraw.Draw(tile)
                td.text((22, 4), last, font=f_big, fill=(0, 0, 0, 170))
                td.text((16, 0), last, font=f_big, fill=INK + (int(255 * min(1, a2 * 3)),))
                if s2 > 1.001:
                    tile = tile.resize((int(tile.width * s2), int(tile.height * s2)), Image.BICUBIC)
                img.alpha_composite(tile, (int((W - tile.width) / 2),
                                           int(NAME_Y + 150 - (tile.height - size_big - 60) / 2)))
                slam = max(0.0, 1 - a2 / 1.6)
            s = (t - tf - 0.42) / 0.22
            if stamp is not None and s > 0:
                z = 1.6 - 0.6 * min(1, ce._back_out(s))
                st = stamp.resize((int(stamp.width * z), int(stamp.height * z)))
                st.putalpha(st.getchannel("A").point(lambda v: int(v * min(1, s * 3))))
                img.alpha_composite(st, ((W - st.width) // 2, NAME_Y + 30 - st.height // 2))
            # the jolt: the whole frame shakes on the freeze and the slam
            shake = max(0.0, 1 - after / 0.25) if t >= tf else 0.0
            shake = max(shake, slam * 0.7)
            frame = img.convert("RGB")
            if shake > 0.02:
                dx, dy = rnd.randint(-14, 14) * shake, rnd.randint(-14, 14) * shake
                frame = ImageChops.offset(_zoom(frame, 1.03, (W / 2, H / 2)), int(dx), int(dy))
            frame.save(os.path.join(work, f"f{fi:04d}.jpg"), quality=90)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS),
                        "-i", os.path.join(work, "f%04d.jpg"),
                        "-vf", "format=yuv420p",
                        "-r", str(FPS), "-c:v", "libx264", "-preset", "medium",
                        "-crf", "19", "-frames:v", str(frames), out_mp4], check=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return out_mp4


def person_card(beat, script, photo, out_mp4, seconds, build_dir):
    """The card for a person beat: their footage up to the name when a
    frame passes every check (cast_engine.find_footage), else the photo.
    Returns (path, credit-or-None) or None (the caller keeps the photo)."""
    if not ce.cutout_available():
        return None
    d = dossier_for(beat.get("subject"), script)
    name = d.get("shown") or d.get("name")
    role = "DECISION MAKER" if d.get("decision") else (d.get("role") or "")
    said = name_time(beat.get("words"), name)
    freeze_at = 0.6 if said is None else said + 0.15
    work = tempfile.mkdtemp(prefix="ncast_", dir=build_dir)
    try:
        foot = None
        if ce.faces_available() and freeze_at >= 0.5:
            try:
                foot = ce.find_footage(d.get("name") or name, photo, script.get("era_year"),
                                       work, freeze_at)
            except Exception as e:
                print(f"   ↩️  {name}: footage search failed ({type(e).__name__}: {e}).")
        if foot:
            try:
                render(name, role, foot["frames"][-1], out_mp4, seconds, freeze_at,
                       lead=foot["frames"][:-1], seed=d.get("file_no") or 1)
                return out_mp4, foot["credit"]
            except Exception as e:
                print(f"   ↩️  {name}: footage card failed ({type(e).__name__}: {e}).")
        render(name, role, Image.open(photo), out_mp4, seconds, freeze_at,
               seed=d.get("file_no") or 1)
        return out_mp4, None
    except Exception as e:
        print(f"   ↩️  {name}: person card failed ({type(e).__name__}: {e}).")
        return None
    finally:
        shutil.rmtree(work, ignore_errors=True)
