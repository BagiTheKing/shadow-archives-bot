"""
Three thumbnails per story-mode video, for YouTube's Test & Compare.

Every run makes up to three different thumbnails from what the video itself
contains, uploads the strongest one, and keeps all three (build/thumbnails/)
so the owner can add the others to Test & Compare in YouTube Studio when the
video goes public (there is no API for starting a test). The looks were
approved from previews on 2026-09-30:

- faceoff: two cast members from opposite sides, cut out (the cast cards'
  cut-out, grey, white outline, red glow) on a dark red duotone archival
  frame, a big hook between them;
- person:  the one who decided, cut out on the right, three short lines on
  the left, the last one in yellow;
- map:     the video's own map places (its map beats' targets and lit
  countries) glowing red on a dark map, big words on the left.

Whatever a video cannot support (no two sides with free portraits, no map
beat) is skipped; the old single-frame thumbnail fills in, so there is always
at least one.
"""

import math
import os
import re

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps

from thumbnail_engine import _cover, _font, _wrap, make_thumbnail, pick_source_scene

W, H = 1280, 720
YELLOW = (255, 216, 0)
RED = (214, 40, 32)
WHITE = (250, 250, 250)
MAX_BYTES = 2 * 1024 * 1024


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------

def _cutout(path, height):
    """(grey portrait, mask) cropped to the person, `height` tall, or None
    when the cut-out is not clean (same check as the cast cards)."""
    import cast_engine as C
    if not C.cutout_available():
        return None
    img = Image.open(path).convert("RGB")
    mask = C.person_mask(img)
    ok, _ = C._mask_ok(mask)
    if not ok:
        return None
    mask = mask.point(lambda v: 255 if v > 128 else int(v * 1.6))
    box = mask.point(lambda v: 255 if v > 100 else 0).getbbox()
    if not box:
        return None
    img, mask = img.crop(box), mask.crop(box)
    scale = height / img.height
    size = (max(1, int(img.width * scale)), height)
    img, mask = img.resize(size, Image.LANCZOS), mask.resize(size, Image.LANCZOS)
    grey = ImageEnhance.Contrast(ImageOps.grayscale(img).convert("RGB")).enhance(1.35)
    return grey, mask


def _paste_person(canvas, cut, xy):
    grey, mask = cut
    glow = mask.filter(ImageFilter.MaxFilter(31)).filter(ImageFilter.GaussianBlur(18))
    canvas.paste(Image.new("RGB", grey.size, RED), xy, glow.point(lambda v: int(v * .5)))
    canvas.paste(Image.new("RGB", grey.size, WHITE), xy, mask.filter(ImageFilter.MaxFilter(19)))
    canvas.paste(grey, xy, mask)


def _background(path, tint=(120, 20, 16)):
    bg = _cover(Image.open(path).convert("RGB"), W, H)
    grey = ImageEnhance.Contrast(ImageOps.grayscale(bg)).enhance(1.3)
    bg = ImageOps.colorize(grey, (6, 4, 4), tint)
    return _vignette(bg)


def _vignette(img):
    vig = Image.new("L", (W, H), 0)
    ImageDraw.Draw(vig).ellipse([-W * .2, -H * .35, W * 1.2, H * 1.35], fill=255)
    vig = vig.filter(ImageFilter.GaussianBlur(W // 8))
    return Image.composite(img, Image.new("RGB", (W, H), (0, 0, 0)), vig)


def _left_scrim(img, reach=.62, strength=235):
    line = Image.new("L", (W, 1))
    px = line.load()
    for x in range(W):
        px[x, 0] = int(strength * max(0.0, 1 - x / (W * reach)) ** 1.4)
    return Image.composite(Image.new("RGB", (W, H), (6, 5, 6)), img, line.resize((W, H)))


def _text(d, s, size, xy, fill, anchor="la", stroke=0):
    f = _font(size)
    d.text((xy[0] + 5, xy[1] + 5), s, font=f, fill=(0, 0, 0), anchor=anchor)
    d.text(xy, s, font=f, fill=fill, anchor=anchor, stroke_width=stroke,
           stroke_fill=(0, 0, 0))


def _fit(d, s, size, width, floor=40):
    while size > floor and d.textlength(s, font=_font(size)) > width:
        size -= 4
    return size


def _year_tag(d, year, xy, anchor_centre=False):
    if not year:
        return
    f = _font(44)
    w = d.textlength(year, font=f) + 44
    x0 = xy[0] - w / 2 if anchor_centre else xy[0]
    d.rectangle([x0, xy[1], x0 + w, xy[1] + 50], fill=RED)
    _text(d, year, 44, (x0 + w / 2, xy[1] + 25), WHITE, "mm")


def _save(img, path):
    q = 92
    while q >= 55:
        img.save(path, "JPEG", quality=q, optimize=True)
        if os.path.getsize(path) <= MAX_BYTES:
            break
        q -= 8
    return path


def faceoff(bg_path, left_photo, right_photo, big, sub, year, out):
    a, b = _cutout(left_photo, 640), _cutout(right_photo, 620)
    if not (a and b):
        return None
    img = _background(bg_path)
    _paste_person(img, a, (-110, H - a[0].height + 40))
    _paste_person(img, b, (W - b[0].width + 110, H - b[0].height + 40))
    d = ImageDraw.Draw(img)
    mid = int(W * 0.44)                   # the gap between the two faces
    size = _fit(d, big, 180, mid)
    _text(d, big, size, (W // 2, 60), YELLOW, "mt", stroke=4)
    y = 60 + int(size * 1.15)
    for line in _wrap(d, sub, _font(70), mid)[:2]:
        _text(d, line, _fit(d, line, 70, mid), (W // 2, y), WHITE, "mt", stroke=3)
        y += 75
    _year_tag(d, year, (W // 2, y + 30), anchor_centre=True)
    return _save(img, out)


def person(bg_path, photo, words, year, out):
    cut = _cutout(photo, 760)
    if not cut:
        return None
    img = _left_scrim(_background(bg_path))
    _paste_person(img, cut, (W - cut[0].width + 30, H - cut[0].height + 80))
    d = ImageDraw.Draw(img)
    width = int(W * 0.5)
    size = 118
    lines = _wrap(d, words, _font(size), width)
    while len(lines) > 3 and size > 60:
        size -= 8
        lines = _wrap(d, words, _font(size), width)
    lines = lines[:3]
    y = (H - len(lines) * int(size * 1.1)) // 2 - 20
    d.rectangle([70, y - 35, 200, y - 25], fill=YELLOW)
    for i, line in enumerate(lines):
        _text(d, line, size, (70, y), YELLOW if i == len(lines) - 1 else WHITE)
        y += int(size * 1.1)
    _year_tag(d, year, (70, y + 20))
    return _save(img, out)


def map_thumb(targets, lit, big, sub, out):
    """targets: place names the video's maps point at; lit: countries or
    provinces they light up. None when no place can be found."""
    import map_engine as M
    regions, points = [], []
    for name in list(dict.fromkeys(targets + lit)):
        r = M.find_region(name)
        if r:
            regions.append((name in targets, r[1]))
        elif name in targets:
            ll = M.geocode(name)
            if ll:
                points.append((name, ll))
    if not regions and not points:
        return None
    # framed on the targets; lit countries are context and may run off the
    # edge (a whole lit Egypt shrank the Sinai to a dot in the first test)
    framed = [rings for is_t, rings in regions if is_t] or [rings for _, rings in regions]
    xs = [p[0] for rings in framed for ring in rings for p in ring] + [ll[0] for _, ll in points]
    ys = [p[1] for rings in framed for ring in rings for p in ring] + [ll[1] for _, ll in points]
    if points and not any(is_t for is_t, _ in regions):
        xs, ys = [ll[0] for _, ll in points], [ll[1] for _, ll in points]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    span = max(max(xs) - min(xs), (max(ys) - min(ys)) * 1.6, 3.5) * 1.35
    k = math.cos(math.radians(cy))
    area_x0, area_w = 420, W - 420               # the map sits right of the text
    scale = area_w / (span * k)
    lon0, lat1 = cx - area_w / 2 / (scale * k), cy + H / 2 / scale

    def P(lon, lat):
        return (area_x0 + (lon - lon0) * k * scale, (lat1 - lat) * scale)

    img = Image.new("RGB", (W, H), (8, 14, 22))
    d = ImageDraw.Draw(img)
    for f in M._load("ne_10m_admin_0_countries"):
        for ring in M._rings(f["geometry"]):
            if any(lon0 - 5 < x < lon0 + W / (scale * k) + 5 and lat1 - H / scale - 5 < y < lat1 + 5
                   for x, y in ring[::25] or ring):
                d.polygon([P(*p) for p in ring], fill=(34, 32, 30), outline=(70, 66, 60))
    # lit countries first, then the red glow, then the targets on top
    for is_target, rings in regions:
        if not is_target:
            for ring in rings:
                d.polygon([P(*p) for p in ring], fill=(90, 86, 80), outline=WHITE)
    glow = Image.new("L", (W, H), 0)
    gd = ImageDraw.Draw(glow)
    for is_target, rings in regions:
        if is_target:
            for ring in rings:
                gd.polygon([P(*p) for p in ring], fill=255)
    for _, ll in points:
        x, y = P(*ll)
        gd.ellipse([x - 70, y - 70, x + 70, y + 70], fill=255)
    img.paste(Image.new("RGB", (W, H), RED), (0, 0),
              glow.filter(ImageFilter.GaussianBlur(16)).point(lambda v: int(v * .75)))
    d = ImageDraw.Draw(img)
    for is_target, rings in regions:
        if is_target:
            for ring in rings:
                d.polygon([P(*p) for p in ring], fill=(190, 34, 28), outline=(255, 120, 100))
    for name, ll in points:
        x, y = P(*ll)
        d.ellipse([x - 24, y - 24, x + 24, y + 24], fill=(190, 34, 28), outline=WHITE, width=5)
        d.ellipse([x - 60, y - 60, x + 60, y + 60], outline=(255, 120, 100), width=3)
    img = _left_scrim(img, reach=.5)
    d = ImageDraw.Draw(img)
    d.rectangle([70, 120, 200, 130], fill=YELLOW)
    size = _fit(d, big, 170, 560)
    _text(d, big, size, (70, 150), YELLOW)
    y = 150 + int(size * 1.15)
    for line in _wrap(d, sub, _font(120), 560)[:2]:
        s = _fit(d, line, 120, 560)
        _text(d, line, s, (70, y), WHITE)
        y += int(s * 1.08)
    return _save(img, out)


# ---------------------------------------------------------------------------
# One video
# ---------------------------------------------------------------------------

def _clean(text, banned):
    t = re.sub(r"\s+", " ", str(text or "")).strip(" .,:;!?\"'").upper()
    return "" if not t or t in banned else t


def _texts(script):
    try:
        from longform_llm import THUMB_EXAMPLE_TEXTS as banned
    except Exception:
        banned = set()
    v = script.get("thumbnail_variants") or {}
    fallback = _clean(script.get("thumbnail_text") or script.get("title"), banned)
    fo = v.get("faceoff") if isinstance(v.get("faceoff"), dict) else {}
    mp = v.get("map") if isinstance(v.get("map"), dict) else {}
    return {
        "faceoff": (_clean(fo.get("big"), banned) or fallback,
                    _clean(fo.get("sub"), banned)),
        "person": _clean(v.get("person"), banned) or fallback,
        "map": (_clean(mp.get("big"), banned) or fallback,
                _clean(mp.get("sub"), banned)),
    }


def _people(scenes):
    """[(name, role, decision, photo)] for every rendered cast card."""
    out, seen = [], set()
    for s in scenes:
        d = s.get("dossier") or {}
        photo = (s.get("image_paths") or [None])[0]
        if (s.get("_is_map") and d.get("name") and photo and os.path.exists(photo)
                and d["name"] not in seen):
            seen.add(d["name"])
            out.append((d["name"], d.get("role") or "", bool(d.get("decision")), photo))
    return out


def build(scenes, script, build_dir):
    """[(kind, path)] best first, all under build_dir/thumbnails/. Empty only
    if not even the plain thumbnail could be drawn."""
    from longform_llm import _side_of
    out_dir = os.path.join(build_dir, "thumbnails")
    os.makedirs(out_dir, exist_ok=True)
    texts = _texts(script)
    year = "".join(re.findall(r"\d", str(script.get("thumbnail_badge") or "")))[:4]
    plain = [s for s in scenes if not s.get("dossier") and not s.get("map")
             and not s.get("_is_map")]
    bg = pick_source_scene(plain) or pick_source_scene(scenes)
    made = []

    people = _people(scenes)
    decider = next((p for p in people if p[2]), people[0] if people else None)
    if bg and len(people) >= 2:
        # two people from different sides, the decider first when there is one
        order = ([decider] if decider else []) + [p for p in people if p is not decider]
        pair = None
        for i, a in enumerate(order):
            for b in order[i + 1:]:
                sa, sb = _side_of({"role": a[1]}), _side_of({"role": b[1]})
                if sa and sb and sa != sb:
                    pair = (a, b)
                    break
            if pair:
                break
        if pair and texts["faceoff"][0]:
            try:
                got = faceoff(bg, pair[0][3], pair[1][3], texts["faceoff"][0],
                              texts["faceoff"][1], year,
                              os.path.join(out_dir, "1_faceoff.jpg"))
                if got:
                    made.append(("faceoff", got))
            except Exception as e:
                print(f"   ↩️  Face-off thumbnail failed ({type(e).__name__}: {e}).")
    if bg and decider and texts["person"]:
        for p in [decider] + [x for x in people if x is not decider]:
            try:
                got = person(bg, p[3], texts["person"], year,
                             os.path.join(out_dir, "2_person.jpg"))
            except Exception as e:
                print(f"   ↩️  Person thumbnail failed ({type(e).__name__}: {e}).")
                got = None
            if got:
                made.append(("person", got))
                break

    targets, lit = [], []
    for s in scenes:
        m = s.get("map") if s.get("_is_map") else None
        if m:
            targets.append(m.get("target") or "")
            lit += list(m.get("light") or [])
    targets = [t for t in targets if t]
    if targets and texts["map"][0]:
        try:
            got = map_thumb(targets, lit, texts["map"][0], texts["map"][1],
                            os.path.join(out_dir, "3_map.jpg"))
            if got:
                made.append(("map", got))
        except Exception as e:
            print(f"   ↩️  Map thumbnail failed ({type(e).__name__}: {e}).")

    if len(made) < 3 and bg:
        # the plain look fills a missing slot, so there is always something
        try:
            got = make_thumbnail(bg, texts["person"] or texts["faceoff"][0],
                                 os.path.join(out_dir, f"{len(made) + 1}_frame.jpg"),
                                 badge=year)
            made.append(("frame", got))
        except Exception as e:
            print(f"   ↩️  Plain thumbnail failed ({type(e).__name__}: {e}).")
    for kind, path in made:
        print(f"🖼️  Thumbnail variant: {kind} — {os.path.basename(path)} "
              f"({os.path.getsize(path) // 1024} KB)")
    return made
