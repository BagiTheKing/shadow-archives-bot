"""
Decision-maker card: one per long-form video, where the person who made
the story's decision is first named.

Approved from the Mao Zedong preview. A case file: the person's portrait as
a photo print (white border, slight tilt, shadow) sliding onto a dark desk,
a worn red DECISION MAKER stamp thumping onto its corner, FILE 01, the
one-line description and lifespan in the stamp typewriter, and the name
typed out exactly as the narrator says it. Named late in the line, the
file reads first and the name lands last; named early, the description
follows it — either way nothing types after the cut.

The portrait and the description come from the person's own Wikipedia
article (its lead image and short description), never from a model. Only
an image hosted on Wikimedia Commons is used (free licence, credited on
screen like every archive photo); a fair-use image, a missing description,
or a page that does not read as a person means no card, and the scene
keeps its photos.
"""

import os
import random
import re
import shutil
import subprocess
import tempfile

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

W, H, FPS = 1920, 1080, 30
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANTON = os.path.join(BASE, "fonts", "Anton-Regular.ttf")
MONO = os.path.join(BASE, "fonts", "FreeMonoBold.ttf")
RED = (205, 58, 46)
INK = (236, 232, 222)
DIM = (170, 166, 156)


def ease(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


def back_out(t):
    """Overshoot then settle — the stamp's thump."""
    t = max(0.0, min(1.0, t))
    c = 1.9
    return 1 + (c + 1) * (t - 1) ** 3 + c * (t - 1) ** 2


def desk():
    rnd = random.Random(3)
    img = Image.new("RGB", (W, H), (22, 21, 19))
    noise = Image.effect_noise((W // 2, H // 2), 22).resize((W, H)).convert("RGB")
    img = Image.blend(img, noise, 0.06)
    # soft pool of light where the file lies
    light = Image.new("L", (W, H), 0)
    ImageDraw.Draw(light).ellipse([-300, -200, W * 0.75, H * 1.2], fill=70)
    light = light.filter(ImageFilter.GaussianBlur(220))
    img = Image.composite(Image.new("RGB", (W, H), (58, 54, 48)), img, light)
    return img


def print_of(photo_path, centering=(0.5, 0.3)):
    """The portrait as a photo print: fitted, desaturated a touch, white
    border, and a shadow layer, returned as one RGBA tile."""
    ph = Image.open(photo_path).convert("RGB")
    ph = ImageOps.fit(ph, (560, 700), centering=centering)
    ph = Image.blend(ph, ImageOps.grayscale(ph).convert("RGB"), 0.4)
    b = 22
    card = Image.new("RGB", (560 + 2 * b, 700 + 2 * b + 30), (232, 228, 216))
    card.paste(ph, (b, b))
    tile = Image.new("RGBA", (card.width + 80, card.height + 80), (0, 0, 0, 0))
    sh = Image.new("RGBA", tile.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).rectangle([48, 52, 48 + card.width, 52 + card.height],
                                 fill=(0, 0, 0, 170))
    tile = Image.alpha_composite(tile, sh.filter(ImageFilter.GaussianBlur(16)))
    tile.paste(card, (40, 40))
    return tile


def stamp_tile(text="DECISION MAKER"):
    f = ImageFont.truetype(MONO, 40)
    tw = int(ImageDraw.Draw(Image.new("L", (1, 1))).textlength(text, font=f))
    im = Image.new("RGBA", (tw + 50, 80), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rectangle([3, 3, im.width - 4, im.height - 4], outline=RED + (235,), width=5)
    d.text((25, 17), text, font=f, fill=RED + (235,))
    # worn ink: knock random specks out of the stamp
    rnd = random.Random(11)
    px = im.load()
    for _ in range(im.width * im.height // 14):
        x, y = rnd.randrange(im.width), rnd.randrange(im.height)
        r, g, bb, a = px[x, y]
        px[x, y] = (r, g, bb, int(a * 0.25))
    return im.rotate(8, expand=True, resample=Image.BICUBIC)


def render(name, lines, photo, out_mp4, duration, name_at, file_no=1,
           centering=(0.5, 0.3)):
    base = desk()
    card = print_of(photo, centering)
    stamp = stamp_tile()
    big = ImageFont.truetype(ANTON, 104)
    small = ImageFont.truetype(MONO, 34)
    tiny = ImageFont.truetype(MONO, 28)
    name = name.upper()
    t_print, t_stamp = 0.0, 0.9
    t_name = max(1.3, name_at)
    t_rule = t_name + len(name) * 0.06 + 0.15
    type_len = sum(len(l) for l in lines) * 0.035
    # When the name is spoken late, the file's description is read first
    # and the name lands last, on the narrator's word; spoken early, the
    # description follows it. Either way nothing types after the cut.
    late = t_name >= 1.0 + type_len + 0.4
    t_lines = 1.0 if late else t_rule + 0.45
    y_name, y_rule, y_lines = (560, 710, 380) if late else (380, 530, 565)
    anim_end = min(duration, max(t_rule + 0.6, t_lines + type_len) + 0.8)
    frames = int(round(anim_end * FPS))
    work = tempfile.mkdtemp(prefix="dossier_")
    try:
        for fi in range(frames):
            t = fi / FPS
            img = base.copy().convert("RGBA")
            # the print slides in from the left and settles at -3 degrees
            a = ease((t - t_print) / 0.7)
            ang = -3 - 6 * (1 - a)
            tile = card.rotate(ang, expand=True, resample=Image.BICUBIC)
            x = int(140 - 500 * (1 - a))
            y = (H - tile.height) // 2
            if a > 0:
                img.alpha_composite(tile, (x, y))
            # the stamp thumps down on the print's lower corner
            s = (t - t_stamp) / 0.28
            if s > 0:
                k = back_out(s)
                sc = 1.6 - 0.6 * min(1.0, k)
                st = stamp.resize((int(stamp.width * sc), int(stamp.height * sc)))
                al = min(1.0, s * 3)
                st.putalpha(st.getchannel("A").point(lambda v: int(v * al)))
                cx, cy = 140 + 470, y + tile.height - 150
                img.alpha_composite(st, (cx - st.width // 2, cy - st.height // 2))

            d = ImageDraw.Draw(img)
            tx = 900
            if t > 0.4:
                d.text((tx, 330), f"FILE {file_no:02d}", font=tiny, fill=RED)
            if t >= t_name:
                n = min(len(name), int((t - t_name) / 0.06) + 1)
                d.text((tx, y_name), name[:n], font=big, fill=INK)
                if n < len(name):
                    # Anton has no block glyph: the cursor is drawn, not typed
                    cx = tx + d.textlength(name[:n], font=big) + 8
                    d.rectangle([cx, y_name + 22, cx + 40, y_name + 128], fill=INK)
            r = ease((t - t_rule) / 0.45)
            if r > 0:
                d.line([(tx, y_rule), (tx + 760 * r, y_rule)], fill=RED, width=4)
            if t >= t_lines:
                budget = (t - t_lines) / 0.035
                yy = y_lines
                for k, ln in enumerate(lines):
                    n = int(min(len(ln), max(0, budget)))
                    if n > 0:
                        d.text((tx, yy), ln[:n], font=small,
                               fill=INK if k == 0 else DIM)
                    budget -= len(ln)
                    yy += 52
            img.convert("RGB").save(os.path.join(work, f"f{fi:04d}.png"))

        hold = max(0.0, duration - frames / FPS)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS),
                        "-i", os.path.join(work, "f%04d.png"),
                        "-vf", f"tpad=stop_mode=clone:stop_duration={hold:.3f},"
                               "zoompan=z='min(1+0.0004*on,1.04)':d=1:s=1920x1080:fps=30,"
                               "format=yuv420p",
                        "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast",
                        "-crf", "18", "-t", f"{duration:.3f}", out_mp4], check=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return out_mp4


WIKI_API = "https://en.wikipedia.org/w/api.php"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
_LIFESPAN = re.compile(r"\(?\s*(?:c\.\s*)?(\d{3,4})\s*[–—-]\s*(\d{3,4})\s*\)?")


def person_file(name, out_dir):
    """{"photo", "lines", "credit"} from the person's Wikipedia article, or
    None. lines = [DESCRIPTION, "1893 – 1976"]."""
    from archive_engine import _polite_get, _strip_html
    r = _polite_get(WIKI_API, params={
        "action": "query", "titles": name, "redirects": 1, "format": "json",
        "prop": "pageimages|description", "piprop": "original|name"},
        timeout=30)
    pages = list(((r.json().get("query") or {}).get("pages") or {}).values())
    if not pages or "missing" in pages[0]:
        print(f"   ↩️  {name}: no Wikipedia article by that name.")
        return None
    page = pages[0]
    desc = (page.get("description") or "").strip()
    # Wikipedia now appends tracking parameters ("...jpg?utm_source=...")
    # to image links; checked as-is, no portrait ever passed and the card
    # never appeared in a published video.
    src = (page.get("original") or {}).get("source", "").split("?")[0]
    file_name = page.get("pageimage", "")
    # Only a Commons image is free to use; a fair-use lead image lives on
    # en.wikipedia and is refused.
    if "/wikipedia/commons/" not in src or not re.search(r"\.(jpe?g|png)$", src, re.I):
        print(f"   ↩️  {name}: no free portrait (lead image {src or 'none'}).")
        return None
    if not desc:
        print(f"   ↩️  {name}: Wikipedia has no short description.")
        return None
    # The years are shown when the description carries them ("Israeli
    # general (1915-1981)"); many leaders' descriptions give a term of
    # office instead ("President of Egypt from 1954 to 1970"), which is
    # kept as written.
    m = _LIFESPAN.search(desc)
    what = (_LIFESPAN.sub("", desc) if m else desc).strip(" ,;()").upper()
    lines = [x for x in (what[:44], f"{m.group(1)} – {m.group(2)}" if m else "") if x]

    img = _polite_get(src, timeout=60)
    img.raise_for_status()
    photo = os.path.join(out_dir, "lf_dossier_photo"
                         + os.path.splitext(src)[1].lower())
    with open(photo, "wb") as f:
        f.write(img.content)
    Image.open(photo).verify()

    credit = f"{file_name.replace('_', ' ')} (via Wikimedia Commons)"
    try:
        info = _polite_get(COMMONS_API, params={
            "action": "query", "titles": f"File:{file_name}", "format": "json",
            "prop": "imageinfo", "iiprop": "extmetadata"}, timeout=30).json()
        meta = next(iter(info["query"]["pages"].values()))["imageinfo"][0]["extmetadata"]
        artist = _strip_html((meta.get("Artist") or {}).get("value", ""))[:60]
        lic = (meta.get("LicenseShortName") or {}).get("value", "")
        credit = (" — ".join(x for x in (file_name.replace("_", " "), artist, lic) if x)
                  + " (via Wikimedia Commons)")
    except Exception:
        pass
    return {"photo": photo, "lines": lines, "credit": credit}


CARD_MAX = 7.0          # every card runs about the same length
CARD_LEAD = 2.4         # the card starts this long before the name
CARD_XFADE = 0.35


def _ken_burns(image, seconds, out):
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-i", image,
         "-vf", "scale=2400:1350:force_original_aspect_ratio=increase,crop=2400:1350,"
                "zoompan=z='min(zoom+0.0006,1.08)':d=1:x='iw/2-(iw/zoom/2)':"
                "y='ih/2-(ih/zoom/2)':s=1920x1080:fps=30,format=yuv420p",
         "-t", f"{seconds:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
         "-r", "30", out], check=True)


def _compose(head_img, head_s, card, card_s, tail_s, out):
    """The scene's own photo, then the card, then the photo again: the card
    keeps its length whatever the scene's."""
    parts, fc, n = [], [], 0
    tmp = []
    if head_s > 0.2:
        h = out + ".head.mp4"
        _ken_burns(head_img, head_s, h)
        parts.append(h)
        tmp.append(h)
    parts.append(card)
    if tail_s > 0.2:
        t = out + ".tail.mp4"
        _ken_burns(head_img, tail_s + CARD_XFADE, t)
        parts.append(t)
        tmp.append(t)
    args = []
    for p in parts:
        args += ["-i", p]
    # same frame rate, timebase and pixel format for every piece, or the
    # crossfade refuses them
    norm = ";".join(f"[{i}:v]fps=30,settb=AVTB,setsar=1,format=yuv420p,"
                    f"scale=1920:1080[n{i}]" for i in range(len(parts)))
    if tail_s > 0.2:
        body = len(parts) - 1
        fc = (f"{norm};{''.join(f'[n{i}]' for i in range(body))}concat=n={body}:v=1[b];"
              f"[b][n{body}]xfade=transition=fade:duration={CARD_XFADE}:"
              f"offset={(head_s if head_s > 0.2 else 0) + card_s - CARD_XFADE:.3f},format=yuv420p[v]")
    else:
        fc = f"{norm};{''.join(f'[n{i}]' for i in range(len(parts)))}concat=n={len(parts)}:v=1,format=yuv420p[v]"
    try:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args, "-filter_complex", fc,
                        "-map", "[v]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                        "-r", "30", out], check=True)
    finally:
        for f in tmp:
            if os.path.exists(f):
                os.remove(f)
    return out


def render_dossier_scene(scene, out_mp4, duration, build_dir, era=None):
    """(path, info) for one scene's card, or None. The scene carries
    "dossier" ({"name", and in story mode "shown", "role", "decision",
    "file_no", "side"}) and "words" (narration timings).

    The cast card (cast_engine: footage or cut-out portrait, freeze on the
    name) is tried first; the case-file card is the fallback. Either way the
    card itself runs at most about CARD_MAX seconds: in a longer scene the
    scene's own photo plays before and after it (the first Six-Day War cut
    held Nasser's card for 13.5 s against 6-8 s for the others)."""
    d = scene.get("dossier") or {}
    name = (d.get("name") or "").strip()
    if not name:
        return None
    try:
        from map_engine import spoken_at
        photo_dir = os.path.join(build_dir, f"cast_{d.get('file_no') or 1:02d}")
        os.makedirs(photo_dir, exist_ok=True)
        info = person_file(name, photo_dir)
        if not info:
            print(f"   ↩️  No card for {name!r}: Wikipedia has no free "
                  f"portrait and description.")
            return None
        surname = (d.get("shown") or name).split()[-1]
        at = spoken_at(scene.get("words") or [], surname)
        if at is None:
            at = spoken_at(scene.get("words") or [], name)
        at = min(at if at is not None else 1.3, max(0.5, duration - 3.0))

        # the card's window inside the scene
        scene_img = (scene.get("image_paths") or [scene.get("image_path")])[0]
        start, card_s = 0.0, duration
        if duration > CARD_MAX + 0.6 and scene_img and os.path.exists(scene_img):
            start = max(0.0, at - CARD_LEAD)
            card_s = min(duration - start, CARD_MAX)
        tail_s = duration - start - card_s
        render_s = card_s + (CARD_XFADE if tail_s > 0.2 else 0.0)
        card_out = out_mp4 + ".card.mp4" if (start > 0.2 or tail_s > 0.2) else out_mp4
        local_at = at - start

        got, kind = None, None
        try:
            from cast_engine import render_cast_scene
            got = render_cast_scene(scene, card_out, render_s, build_dir, info, local_at, era)
        except Exception as e:
            print(f"   ↩️  Cast card unavailable ({type(e).__name__}: {e}).")
            got = None
        if got:
            _, credit, kind = got
            info = {**info, "credit": credit}
            scene["_cast_freeze"] = at
            print(f"🎭 Cast card ({kind}): {d.get('shown') or name} — "
                  f"{'DECISION MAKER' if d.get('decision') else d.get('role') or ''}"
                  f" ({card_s:.1f}s of a {duration:.1f}s scene).")
        else:
            render(d.get("shown") or name, info["lines"], info["photo"], card_out, render_s,
                   name_at=local_at, file_no=d.get("file_no") or 1)
            print(f"🗂️  Case-file card: {name} — {' · '.join(info['lines'])}.")
        if card_out != out_mp4:
            _compose(scene_img, start, card_out, card_s, tail_s, out_mp4)
            os.remove(card_out)
            info = {**info, "scene_photo": scene_img}
        return out_mp4, info
    except Exception as e:
        print(f"⚠️ Card for {name!r} skipped ({type(e).__name__}: {e}) — "
              f"keeping the scene's photos.")
        return None
