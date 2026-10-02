"""
Three thumbnails per story-mode video, tried in turn by the thumbnail
rotation (tools/thumb_rotation.py) or by YouTube's Test & Compare.

The look (approved 2026-09-30 on the Ethiopia–Eritrea set): a dark map of
the two sides with their border glowing red, one shocking and verified fact
in huge type that a stranger understands at a glance, and plain tags that say
who is who ("ETHIOPIA", "ERITREA", "ETHIOPIA'S LEADER").

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



def _tag(d, text, x, y):
    """A small boxed label centred on x ("ETHIOPIA", "EGYPT'S PRESIDENT")."""
    if not text:
        return
    f = _font(46)
    while f.size > 28 and d.textlength(text, font=f) > 420:
        f = _font(f.size - 4)
    w = d.textlength(text, font=f) + 36
    d.rectangle([x - w / 2, y, x + w / 2, y + 58], fill=(10, 10, 10), outline=WHITE, width=3)
    d.text((x, y + 29), text, font=f, fill=WHITE, anchor="mm")


def _box(d, text, x, y, size=62, max_w=520):
    """White words on a red bar, centred on x."""
    if not text:
        return
    size = _fit(d, text, size, max_w - 40, floor=30)
    f = _font(size)
    w = d.textlength(text, font=f) + 48
    d.rectangle([x - w / 2, y, x + w / 2, y + size + 22], fill=RED)
    d.text((x, y + (size + 22) / 2), text, font=f, fill=WHITE, anchor="mm")


# ---------------------------------------------------------------------------
# The map: every look is drawn on it when the story's places can be found
# ---------------------------------------------------------------------------

def _country(name):
    import map_engine as M
    n = M._norm(name)
    for f in M._load("ne_10m_admin_0_countries"):
        p = f["properties"]
        if n and n in {M._norm(p.get(k)) for k in ("ADMIN", "NAME", "NAME_LONG", "NAME_EN")}:
            return f
    return None


def _border(a, b):
    """The shared border of two countries as lists of (lon, lat), or []."""
    import map_engine as M
    fa, fb = _country(a), _country(b)
    if not (fa and fb):
        return []
    pts_b = {(round(x, 2), round(y, 2)) for r in M._rings(fb["geometry"]) for x, y in r}
    segs = []
    for r in M._rings(fa["geometry"]):
        seg = []
        for x, y in r:
            if (round(x, 2), round(y, 2)) in pts_b:
                seg.append((x, y))
            else:
                if len(seg) > 1:
                    segs.append(seg)
                seg = []
        if len(seg) > 1:
            segs.append(seg)
    return segs


class Stage:
    """A dark map framed on (cx, cy) spanning `span` degrees of longitude
    across the area right of x0."""

    def __init__(self, cx, cy, span, x0=0):
        self.k = math.cos(math.radians(cy))
        self.scale = (W - x0) / (span * self.k)
        self.x0 = x0
        self.lon0 = cx - (W - x0) / 2 / (self.scale * self.k)
        self.lat1 = cy + H / 2 / self.scale

    def P(self, lon, lat):
        return (self.x0 + (lon - self.lon0) * self.k * self.scale,
                (self.lat1 - lat) * self.scale)

    def visible(self, ring):
        lon1 = self.lon0 + (W - self.x0) / (self.scale * self.k) + 2
        lat0 = self.lat1 - H / self.scale - 2
        return any(self.lon0 - 2 < x < lon1 and lat0 < y < self.lat1 + 2
                   for x, y in ring[::20] or ring)


def _draw_map(stage, sides=(), targets=(), points=(), labels=True, dim=1.0):
    """The map image: land, the two sides a shade lighter, their border
    glowing red, target regions red, target points ringed."""
    import map_engine as M
    img = Image.new("RGB", (W, H), (7, 10, 16))
    d = ImageDraw.Draw(img)
    side_names = {M._norm(s) for s in sides}
    for f in M._load("ne_10m_admin_0_countries"):
        p = f["properties"]
        mine = M._norm(p.get("NAME")) in side_names or M._norm(p.get("ADMIN")) in side_names
        for r in M._rings(f["geometry"]):
            if stage.visible(r):
                d.polygon([stage.P(*q) for q in r], fill=(56, 50, 44) if mine else (28, 27, 26),
                          outline=(84, 78, 70))
    glow = Image.new("L", (W, H), 0)
    gd = ImageDraw.Draw(glow)
    if len(sides) >= 2:
        for seg in _border(sides[0], sides[1]):
            gd.line([stage.P(*q) for q in seg], fill=255, width=10)
    for rings in targets:
        for r in rings:
            gd.polygon([stage.P(*q) for q in r], fill=255)
    for ll in points:
        x, y = stage.P(*ll)
        gd.ellipse([x - 40, y - 40, x + 40, y + 40], fill=255)
    img.paste(Image.new("RGB", (W, H), RED), (0, 0), glow.filter(ImageFilter.GaussianBlur(12)))
    d = ImageDraw.Draw(img)
    for rings in targets:
        for r in rings:
            d.polygon([stage.P(*q) for q in r], fill=(190, 34, 28), outline=(255, 120, 100))
    if len(sides) >= 2:
        for seg in _border(sides[0], sides[1]):
            d.line([stage.P(*q) for q in seg], fill=(255, 110, 90), width=3)
    if labels:
        for s in sides:
            f = _country(s)
            if f:
                x, y = stage.P(f["properties"]["LABEL_X"], f["properties"]["LABEL_Y"])
                if 0 < x < W and 0 < y < H:
                    d.text((x, y), s.upper(), font=_font(40), fill=(235, 225, 210), anchor="mm")
    if dim < 1:
        img = Image.blend(Image.new("RGB", (W, H), (0, 0, 0)), img, dim)
    return img


def _frame(sides, targets, points):
    """(cx, cy, span) around the targets, else the two sides' border."""
    xs, ys = [], []
    for rings in targets:
        for r in rings:
            xs += [q[0] for q in r]
            ys += [q[1] for q in r]
    for ll in points:
        xs.append(ll[0])
        ys.append(ll[1])
    if not xs and len(sides) >= 2:
        for seg in _border(sides[0], sides[1]):
            xs += [q[0] for q in seg]
            ys += [q[1] for q in seg]
    if not xs:
        return None
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    span = max(max(xs) - min(xs), (max(ys) - min(ys)) * 1.6, 3.5) * 1.35
    return cx, cy, span


# ---------------------------------------------------------------------------
# The three looks
# ---------------------------------------------------------------------------

def faceoff(bg, left, right, big, box, tags, out):
    """bg: a finished 1280x720 background. left/right: (photo, tag)."""
    a, b = _cutout(left, 650), _cutout(right, 630)
    if not (a and b):
        return None
    img = bg.copy()
    _paste_person(img, a, (-150, H - a[0].height + 40))
    _paste_person(img, b, (W - b[0].width + 120, H - b[0].height + 40))
    d = ImageDraw.Draw(img)
    mid = int(W * 0.46)
    words = big.split()
    # the number (or first word) huge and yellow, the rest large and white
    first = words[0] if len(words) > 1 else big
    rest = " ".join(words[1:]) if len(words) > 1 else ""
    s1 = _fit(d, first, 200, mid)
    _text(d, first, s1, (W // 2, 40), YELLOW, "mt", stroke=5)
    y = 40 + int(s1 * 1.07)
    if rest:
        s2 = _fit(d, rest, 150, mid)
        _text(d, rest, s2, (W // 2, y), WHITE, "mt", stroke=5)
        y += int(s2 * 1.2)
    _box(d, box, W // 2, max(y, 430), max_w=mid + 40)
    _tag(d, tags[0], 190, 640)
    _tag(d, tags[1], W - 190, 640)
    return _save(img, out)


def person(bg, photo, setup, twist, tag, out):
    cut = _cutout(photo, 760)
    if not cut:
        return None
    img = _left_scrim(bg.copy())
    _paste_person(img, cut, (W - cut[0].width + 60, H - cut[0].height + 80))
    d = ImageDraw.Draw(img)
    width = int(W * 0.52)
    y = 165
    d.rectangle([70, y - 35, 200, y - 25], fill=YELLOW)
    for text, colour, size in ((setup, WHITE, 128), (twist, YELLOW, 96)):
        lines = _wrap(d, text, _font(size), width)
        while len(lines) > 2 and size > 60:
            size -= 8
            lines = _wrap(d, text, _font(size), width)
        for line in lines[:2]:
            _text(d, line, size, (70, y), colour)
            y += int(size * 1.08)
        y += 20
    _tag(d, tag, W - 260, 640)
    return _save(img, out)


def map_look(stage_args, sides, targets, points, label, question, tag, out):
    """The question on the left, an arrow to the place, the tag under it."""
    # closer than the backgrounds: the place is the subject here
    st = Stage(stage_args[0], stage_args[1], stage_args[2] * 0.72, x0=380)
    img = _draw_map(st, sides, targets, points)
    d = ImageDraw.Draw(img)
    if points:
        tx, ty = st.P(*points[0])
    elif targets:
        r = max(targets[0], key=len)
        tx = sum(st.P(*q)[0] for q in r) / len(r)
        ty = sum(st.P(*q)[1] for q in r) / len(r)
    else:
        tx = ty = None
    if tx is not None and points:
        d.ellipse([tx - 20, ty - 20, tx + 20, ty + 20], fill=(200, 30, 24), outline=WHITE, width=6)
        d.ellipse([tx - 58, ty - 58, tx + 58, ty + 58], outline=WHITE, width=4)
    img = _left_scrim(img, reach=.52)
    d = ImageDraw.Draw(img)
    if tx is not None and tx > 620:
        d.line([(560, ty), (tx - 70, ty)], fill=YELLOW, width=12)
        d.polygon([(tx - 66, ty), (tx - 100, ty - 24), (tx - 100, ty + 24)], fill=YELLOW)
        if label:
            d.text((tx, ty + 78), label.upper(), font=_font(34), fill=WHITE, anchor="mm")
    d.rectangle([70, 110, 200, 120], fill=YELLOW)
    size = 124
    lines = _wrap(d, question, _font(size), 540)
    while len(lines) > 3 and size > 70:
        size -= 8
        lines = _wrap(d, question, _font(size), 540)
    y = 140
    for i, line in enumerate(lines[:3]):
        _text(d, line, size, (70, y), YELLOW if i == len(lines[:3]) - 1 else WHITE)
        y += int(size * 1.12)
    if tag:
        f = _font(50)
        w = d.textlength(tag, font=f) + 48
        d.rectangle([70, y + 20, 70 + w, y + 80], fill=RED)
        d.text((70 + w / 2, y + 50), tag, font=f, fill=WHITE, anchor="mm")
    return _save(img, out)


# ---------------------------------------------------------------------------
# One video
# ---------------------------------------------------------------------------

def _clean(text, banned=()):
    t = re.sub(r"\s+", " ", str(text or "")).strip(" ,:;\"'").upper()
    return "" if not t or t in banned else t


def _texts(script):
    """The three looks' words (validated in longform_llm against the fact
    sheet); what is missing falls back to the single thumbnail text."""
    try:
        from longform_llm import THUMB_EXAMPLE_TEXTS as banned
    except Exception:
        banned = set()
    v = script.get("thumbnail_variants") or {}
    get = lambda k: v.get(k) if isinstance(v.get(k), dict) else {}
    fallback = _clean(script.get("thumbnail_text") or script.get("title"), banned)
    fo, pe, mp = get("faceoff"), get("person"), get("map")
    return {
        "big": _clean(fo.get("big"), banned) or fallback,
        "box": _clean(fo.get("box") or fo.get("sub"), banned),
        "setup": _clean(pe.get("setup"), banned) or fallback,
        "twist": _clean(pe.get("twist"), banned),
        "question": _clean(mp.get("question") or mp.get("big"), banned) or fallback,
        "tag": _clean(mp.get("tag") or mp.get("sub"), banned),
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


def _side_name(role):
    """The country a cast member's role names, as the map spells it:
    "Egypt's president" -> "Egypt", "Ethiopian Prime Minister" ->
    "Ethiopia", "Israeli general" -> "Israel". "" when none is found."""
    for w in re.findall(r"[A-Z][A-Za-z-]+", role or ""):
        if w.lower() in {"king", "queen", "president", "prime", "minister", "general",
                         "field", "marshal", "chief", "the", "of", "defense", "defence"}:
            continue
        for cand in (w, w[:-1], w[:-2], w[:-3], w[:-1] + "a"):
            try:
                if len(cand) > 3 and _country(cand):
                    return _country(cand)["properties"]["NAME"]
            except Exception:
                return ""
    return ""


def _short_tag(role, limit=26):
    """The role as a tag of at most `limit` characters, cut at a word and
    never ending on a joining word ("DEFENDING ATTORNEY AND FUT" went on
    the 2026-10-02 thumbnail)."""
    words, out = (role or "").upper().split(), []
    for w in words:
        if len(" ".join(out + [w])) > limit:
            break
        out.append(w)
    while out and out[-1] in ("AND", "OF", "THE", "FOR", "TO", "IN", "WHO", "A", "&", ","):
        out.pop()
    return " ".join(out).strip(" ,;-")


def build(scenes, script, build_dir):
    """[(kind, path)] best first, all under build_dir/thumbnails/. Empty only
    if not even the plain thumbnail could be drawn."""
    import map_engine as M
    out_dir = os.path.join(build_dir, "thumbnails")
    os.makedirs(out_dir, exist_ok=True)
    t = _texts(script)
    plain = [s for s in scenes if not s.get("dossier") and not s.get("map")
             and not s.get("_is_map")]
    bg_photo = pick_source_scene(plain) or pick_source_scene(scenes)
    made = []

    people = _people(scenes)
    decider = next((p for p in people if p[2]), people[0] if people else None)
    pair = None
    order = ([decider] if decider else []) + [p for p in people if p is not decider]
    for i, a in enumerate(order):
        for b in order[i + 1:]:
            sa, sb = _side_name(a[1]), _side_name(b[1])
            if sa and sb and sa.lower() != sb.lower():
                pair = (a, b)
                break
        if pair:
            break
    sides = [_side_name(p[1]) for p in pair] if pair else \
        [x for x in dict.fromkeys(_side_name(p[1]) for p in people) if x][:2]

    # the video's own map places
    target_names, lit = [], []
    for s in scenes:
        m = s.get("map") if s.get("_is_map") else None
        if m and m.get("target"):
            target_names.append(m["target"])
            lit += list(m.get("light") or [])
    targets, points, label = [], [], ""
    for name in dict.fromkeys(target_names):
        try:
            r = M.find_region(name)
            if r and r[0] == "province":
                targets.append(r[1])
                label = label or name
            elif not r:
                ll = M.geocode(name)
                if ll:
                    points.append(ll)
                    label = label or name
        except Exception as e:
            print(f"   ↩️  Map place {name!r} not found ({type(e).__name__}).")
    sides = [s for s in sides if _country(s)] or [x for x in lit if _country(x)][:2]

    # the background: the map of the two sides when it can be drawn, the
    # video's archival frame otherwise
    fr = _frame(sides, targets, points)
    if fr:
        bg = _vignette(_draw_map(Stage(fr[0], fr[1], max(fr[2], 6.0)), sides,
                                 targets, points, labels=False, dim=.75))
    elif bg_photo:
        bg = _background(bg_photo)
    else:
        bg = None

    if bg is not None and pair and t["big"]:
        try:
            got = faceoff(bg, pair[0][3], pair[1][3], t["big"], t["box"],
                          (_side_name(pair[0][1]).upper(), _side_name(pair[1][1]).upper()),
                          os.path.join(out_dir, "1_faceoff.jpg"))
            if got:
                made.append(("faceoff", got))
        except Exception as e:
            print(f"   ↩️  Face-off thumbnail failed ({type(e).__name__}: {e}).")
    if fr and t["question"]:
        try:
            got = map_look(fr, sides, targets, points, label, t["question"], t["tag"],
                           os.path.join(out_dir, "2_map.jpg"))
            if got:
                made.append(("map", got))
        except Exception as e:
            print(f"   ↩️  Map thumbnail failed ({type(e).__name__}: {e}).")
    if bg is not None and decider and t["setup"]:
        for p in [decider] + [x for x in people if x is not decider]:
            try:
                got = person(bg, p[3], t["setup"], t["twist"], _short_tag(p[1]),
                             os.path.join(out_dir, "3_person.jpg"))
            except Exception as e:
                print(f"   ↩️  Person thumbnail failed ({type(e).__name__}: {e}).")
                got = None
            if got:
                made.append(("person", got))
                break

    if len(made) < 3 and bg_photo:
        # the plain look fills a missing slot, so there is always something
        try:
            year = "".join(re.findall(r"\d", str(script.get("thumbnail_badge") or "")))[:4]
            got = make_thumbnail(bg_photo, t["big"] or t["setup"],
                                 os.path.join(out_dir, f"{len(made) + 1}_frame.jpg"),
                                 badge=year)
            made.append(("frame", got))
        except Exception as e:
            print(f"   ↩️  Plain thumbnail failed ({type(e).__name__}: {e}).")
    for kind, path in made:
        print(f"🖼️  Thumbnail variant: {kind} — {os.path.basename(path)} "
              f"({os.path.getsize(path) // 1024} KB)")
    return made
