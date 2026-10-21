"""
Animated map beat: one short documentary map per long-form video, under the
line that first names the story's key place.

Approved from the preview (tools-era scratch render, Kinmen 1958): the
camera opens wide on the region, pushes in close as the place is spoken,
red strike arcs (or a movement arrow) land on it with a pulsing ring, then
it pulls back to the country the story points at, lit in a neutral tone.

RULES THAT CAME FROM THE PREVIEW
- Red is the ACTION only (arcs, arrow, ring). A country is never filled red:
  on a story about one state shelling another, a red country reads as a side.
- A map takes no photo grade (a sepia grade turned the red arcs grey). Its
  base palette follows the chapter's tone instead; red stays red.
- Before 1900 modern borders are wrong, so old stories get coastlines and
  labels only: no borders, no country highlights.

WHERE THE DATA COMES FROM
- Land, countries and provinces: Natural Earth 1:10m (public domain), fetched
  once from the project's GitHub mirror into NATURAL_EARTH_DIR (cached by the
  workflows).
- A place's position: Wikipedia's own coordinates for that article, then
  Natural Earth's populated places, then its country/province label points.
  The model never supplies a coordinate.

Every failure returns None and the scene keeps its photos: a missing map
costs one line its graphic, never the video.
"""

import json
import math
import os
import random
import re
import shutil
import subprocess
import tempfile

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H, FPS = 1920, 1080, 30
SS = 2                       # supersampling for smooth edges

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.getenv("NATURAL_EARTH_DIR",
                     os.path.join(BASE_DIR, "models", "naturalearth"))
FONT_PATH = os.path.join(BASE_DIR, "fonts", "FreeMonoBold.ttf")
NE_URL = ("https://raw.githubusercontent.com/nvkelso/natural-earth-vector/"
          "master/geojson/{}.geojson")
NE_FILES = ("ne_10m_admin_0_countries", "ne_10m_admin_1_states_provinces",
            "ne_10m_populated_places_simple")
WIKI_API = "https://en.wikipedia.org/w/api.php"
MODERN_BORDERS_FROM = 1900

RED = (205, 58, 46)
PALETTES = {
    # Lifted a step from the preview's values: the final pass adds its own
    # vignette and contrast, and the preview's tones went dim under it.
    "neutral": dict(SEA=(14, 17, 20), LAND=(52, 52, 49), LAND_EDGE=(104, 100, 92),
                    SOFT_FILL=(72, 68, 61), LIT_FILL=(98, 94, 86),
                    LIT_EDGE=(196, 188, 170), LABEL=(232, 232, 226),
                    SEA_LABEL=(120, 132, 140)),
    "warm": dict(SEA=(21, 18, 14), LAND=(64, 58, 48), LAND_EDGE=(130, 118, 96),
                 SOFT_FILL=(86, 77, 62), LIT_FILL=(116, 105, 87),
                 LIT_EDGE=(214, 200, 170), LABEL=(238, 230, 212),
                 SEA_LABEL=(146, 134, 112)),
    "cold": dict(SEA=(12, 16, 22), LAND=(46, 51, 56), LAND_EDGE=(96, 106, 116),
                 SOFT_FILL=(64, 70, 77), LIT_FILL=(88, 96, 104),
                 LIT_EDGE=(176, 190, 204), LABEL=(226, 232, 238),
                 SEA_LABEL=(110, 128, 144)),
}
# Same roles as longform_video.GRADE_BY_ROLE.
TONE_BY_ROLE = {"open": "cold", "build": "warm", "drive": "cold"}


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

_DATA = {}


def _lat_span():
    """How much latitude the land crop keeps around the camera, per unit of
    its width: 0.7 for the 16:9 frame, about 2.2 for a 9:16 one
    (native_short.py renders maps vertical by setting W and H)."""
    return 0.7 * (16 / 9) / (W / H)


def _load(name):
    if name in _DATA:
        return _DATA[name]
    path = os.path.join(DATA_DIR, f"{name}.geojson")
    if not os.path.exists(path):
        import requests
        os.makedirs(DATA_DIR, exist_ok=True)
        r = requests.get(NE_URL.format(name), timeout=120)
        r.raise_for_status()
        with open(path + ".part", "wb") as f:
            f.write(r.content)
        os.replace(path + ".part", path)
    with open(path, encoding="utf-8") as f:
        _DATA[name] = json.load(f)["features"]
    return _DATA[name]


def _norm(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _rings(geom):
    polys = geom["coordinates"] if geom["type"] == "MultiPolygon" \
        else [geom["coordinates"]]
    return [poly[0] for poly in polys]


def find_region(name):
    """(kind, rings, label_lonlat) for a country or province, or None."""
    n = _norm(name)
    if not n:
        return None
    for f in _load("ne_10m_admin_0_countries"):
        p = f["properties"]
        if n in {_norm(p.get(k)) for k in ("ADMIN", "NAME", "NAME_LONG", "NAME_EN")}:
            return ("country", _rings(f["geometry"]), (p["LABEL_X"], p["LABEL_Y"]))
    for f in _load("ne_10m_admin_1_states_provinces"):
        p = f["properties"]
        if n in {_norm(p.get("name")), _norm(p.get("name_en"))}:
            return ("province", _rings(f["geometry"]),
                    (p["longitude"], p["latitude"]))
    return None


def _wikipedia_coords(name):
    try:
        from archive_engine import _polite_get
        r = _polite_get(WIKI_API, params={
            "action": "query", "prop": "coordinates", "titles": name,
            "redirects": 1, "format": "json", "colimit": 1}, timeout=30)
        for page in (r.json().get("query") or {}).get("pages", {}).values():
            for c in page.get("coordinates") or []:
                return (float(c["lon"]), float(c["lat"]))
    except Exception as e:
        print(f"   ⚠️ Wikipedia coordinates unavailable for {name!r} "
              f"({type(e).__name__}).")
    return None


# Broad regions a story names that have no point of their own: Wikipedia
# gives "North Africa" no coordinates and Natural Earth has no such place
# (the 2026-10-01 Rommel video lost its map to exactly this). Centres, lon/lat.
MACRO_REGIONS = {
    "north africa": (17.0, 28.0), "east africa": (38.0, 2.0),
    "west africa": (-3.0, 12.0), "southern africa": (25.0, -25.0),
    "horn of africa": (45.0, 8.0), "middle east": (45.0, 29.0),
    "near east": (38.0, 33.0), "levant": (36.0, 33.5),
    "persian gulf": (51.5, 26.5), "the gulf": (51.5, 26.5),
    "western front": (3.5, 49.8), "eastern front": (30.0, 52.0),
    "balkans": (21.0, 43.0), "caucasus": (44.0, 42.5),
    "central asia": (66.0, 43.0), "southeast asia": (110.0, 10.0),
    "far east": (125.0, 35.0), "mediterranean": (18.0, 36.0),
    "mediterranean sea": (18.0, 36.0), "pacific": (165.0, 10.0),
    "pacific ocean": (165.0, 10.0), "atlantic": (-35.0, 35.0),
    "atlantic ocean": (-35.0, 35.0), "scandinavia": (15.0, 63.0),
    "indochina": (104.0, 15.0), "latin america": (-65.0, -10.0),
    "central america": (-87.0, 14.0), "western europe": (3.0, 48.0),
    "eastern europe": (27.0, 52.0), "sinai": (33.8, 29.5),
    "sinai peninsula": (33.8, 29.5), "golan heights": (35.75, 33.0),
    "suez canal": (32.35, 30.6), "the straits of tiran": (34.46, 27.97),
    "straits of tiran": (34.46, 27.97), "english channel": (-1.5, 50.2),
}

# The broad regions made of countries are lit as those countries, like a
# single country is (names as Natural Earth writes them).
MACRO_MEMBERS = {
    "north africa": ["Morocco", "Algeria", "Tunisia", "Libya", "Egypt"],
    "middle east": ["Egypt", "Israel", "Palestine", "Jordan", "Lebanon", "Syria", "Iraq",
                    "Iran", "Saudi Arabia", "Kuwait", "Qatar", "Bahrain",
                    "United Arab Emirates", "Oman", "Yemen", "Turkey"],
    "near east": ["Israel", "Palestine", "Jordan", "Lebanon", "Syria", "Iraq", "Turkey"],
    "levant": ["Israel", "Palestine", "Jordan", "Lebanon", "Syria"],
    "horn of africa": ["Ethiopia", "Eritrea", "Djibouti", "Somalia", "Somaliland"],
    "east africa": ["Kenya", "Uganda", "Tanzania", "Rwanda", "Burundi", "Ethiopia", "Somalia"],
    "west africa": ["Senegal", "Mali", "Guinea", "Ivory Coast", "Ghana", "Burkina Faso",
                    "Niger", "Nigeria", "Benin", "Togo", "Sierra Leone", "Liberia"],
    "western front": ["France", "Belgium"],
    "eastern front": ["Poland", "Belarus", "Ukraine", "Lithuania", "Latvia", "Estonia"],
    "balkans": ["Slovenia", "Croatia", "Bosnia and Herzegovina", "Serbia", "Montenegro",
                "Kosovo", "North Macedonia", "Albania", "Bulgaria", "Greece"],
    "caucasus": ["Georgia", "Armenia", "Azerbaijan"],
    "scandinavia": ["Norway", "Sweden", "Denmark"],
    "indochina": ["Vietnam", "Laos", "Cambodia"],
    "persian gulf": ["Kuwait", "Qatar", "Bahrain", "United Arab Emirates"],
    "the gulf": ["Kuwait", "Qatar", "Bahrain", "United Arab Emirates"],
}

_GEOCODED = {}


def _geo_region(name):
    """A named physical region (Sinai Peninsula, Sahara, Crimea,
    Normandie) from Natural Earth: the centre of its largest outline."""
    n = _norm(name)
    full = lambda s: _norm((s or "").replace("Pen.", "Peninsula").replace("PEN.", "PENINSULA")
                           .replace("Mts.", "Mountains").replace("MTS.", "MOUNTAINS"))
    try:
        feats = _load("ne_10m_geography_regions_polys")
    except Exception:
        return None
    for f in feats:
        p = f["properties"]
        if n in {full(p.get(k)) for k in ("NAME", "NAME_EN", "NAMEALT")} - {""}:
            ring = max(_rings(f["geometry"]), key=len)
            xs, ys = [q[0] for q in ring], [q[1] for q in ring]
            return ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)
    return None


def _geo_region_rings(name):
    """The outline rings of a named physical region, or None."""
    n = _norm(name)
    full = lambda s: _norm((s or "").replace("Pen.", "Peninsula").replace("PEN.", "PENINSULA")
                           .replace("Mts.", "Mountains").replace("MTS.", "MOUNTAINS"))
    try:
        feats = _load("ne_10m_geography_regions_polys")
    except Exception:
        return None
    for f in feats:
        p = f["properties"]
        if n in {full(p.get(k)) for k in ("NAME", "NAME_EN", "NAMEALT")} - {""}:
            return _rings(f["geometry"])
    return None


def _region_spec(plan, target_ll, words, duration, modern):
    """A country, province or broad region as the target (Germany, North
    Africa): the whole region framed and lit, its name in the middle, in
    place of a pinpoint zoomed into its empty centre."""
    name = plan["target"]
    key = _norm(re.sub(r"^the\s+", "", name, flags=re.I))
    rings = None
    r = find_region(name)
    if r and modern:
        rings = r[1]
    elif key in MACRO_MEMBERS:
        rings = []
        for member in MACRO_MEMBERS[key]:
            got = find_region(member)
            if got:
                main = max(got[1], key=len)
                rings += [g for g in got[1] if _km(g[0], main[0]) < 1500]
        rings = rings or None
        r = None
    elif not r:
        rings = _geo_region_rings(name)
    if rings and key in MACRO_MEMBERS:
        xs = [p[0] for g in rings for p in g]
        ys = [p[1] for g in rings for p in g]
        cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
        fit = max(max(xs) - min(xs),
                  (max(ys) - min(ys)) * (W / H) / math.cos(math.radians(cy))) * 1.3
    elif rings:
        # the mainland, not overseas islands far from it
        main = max(rings, key=len)
        mx, my = sum(p[0] for p in main) / len(main), sum(p[1] for p in main) / len(main)
        rings = [g for g in rings if _km(g[0], (mx, my)) < 1500]
        xs = [p[0] for g in rings for p in g]
        ys = [p[1] for g in rings for p in g]
        cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
        fit = max(max(xs) - min(xs),
                  (max(ys) - min(ys)) * (W / H) / math.cos(math.radians(cy))) * 1.5
    else:
        cx, cy = target_ll
        # a sea or an ocean wide, a small feature (Sinai, the Golan) close
        fit = 28.0 if re.search(r"\b(sea|ocean|atlantic|pacific|mediterranean|asia|europe|america|east)\b", key) else 9.0
    fit = max(4.0, min(60.0, fit))
    t_target = spoken_at(words, name)
    t_target = min(max(t_target if t_target is not None else duration * 0.3, 1.2),
                   max(1.2, duration - 3.0))
    wide = (cx, cy, min(90.0, fit * 2.2))
    close = (cx, cy, fit)
    camera = [(0.0, wide), (max(0.9, t_target - 0.1), close), (t_target + 3.0, close)]
    label_ll = r[2] if (r and rings) else (cx, cy)
    highlights = [{"rings": rings, "style": "lit", "at": t_target}] if rings else []
    targets = [] if rings else [{"ll": (cx, cy), "at": t_target + 0.45}]
    labels = [{"text": name.upper(), "ll": label_ll, "at": t_target + 0.45,
               "anchor": "c" if rings else "l"}]
    margin = wide[2]
    return {
        "bbox": (cx - margin, cy - margin * _lat_span(), cx + margin, cy + margin * _lat_span()),
        "camera": camera, "highlights": highlights, "arrows": [],
        "targets": targets or [{"ll": (cx, cy), "at": 1e9}],
        "labels": labels, "borders": modern,
        "anim_end": min(duration, t_target + 0.45 + len(name) * 0.055 + 1.5),
    }


def _is_region(name):
    n = _norm(re.sub(r"^the\s+", "", name or "", flags=re.I))
    return bool(find_region(name) or _geo_region_rings(name) or n in MACRO_REGIONS)


def _wikipedia_search_coords(name):
    """The first of Wikipedia's top search hits for `name` that has
    coordinates and whose title carries the name's first word."""
    try:
        from archive_engine import _polite_get
        r = _polite_get(WIKI_API, params={
            "action": "query", "generator": "search", "gsrsearch": name,
            "gsrlimit": 3, "prop": "coordinates", "colimit": 3,
            "format": "json"}, timeout=30)
        pages = sorted(((r.json().get("query") or {}).get("pages") or {}).values(),
                       key=lambda pg: pg.get("index", 9))
        first = _norm(name).split()[0] if _norm(name) else ""
        for pg in pages:
            if first and first in _norm(pg.get("title")).split() and pg.get("coordinates"):
                c = pg["coordinates"][0]
                return (float(c["lon"]), float(c["lat"]))
    except Exception as e:
        print(f"   ⚠️ Wikipedia search unavailable for {name!r} ({type(e).__name__}).")
    return None


def geocode(name):
    """(lon, lat) for a named place, or None. Wikipedia first — it knows
    islands, battlefields and bases that no city list has — then Natural
    Earth's cities, countries, provinces and physical regions, the broad
    regions above, and last a Wikipedia search. Remembered for the run."""
    key = _norm(re.sub(r"^the\s+", "", (name or "").strip(), flags=re.I))
    if not key:
        return None
    if key in _GEOCODED:
        return _GEOCODED[key]
    ll = _wikipedia_coords(name)
    if not ll:
        for f in _load("ne_10m_populated_places_simple"):
            p = f["properties"]
            if key in {_norm(p.get("name")), _norm(p.get("nameascii"))}:
                ll = tuple(f["geometry"]["coordinates"][:2])
                break
    if not ll:
        region = find_region(name)
        ll = region[2] if region else None
    ll = ll or _geo_region(name) or MACRO_REGIONS.get(key) or _wikipedia_search_coords(name)
    _GEOCODED[key] = tuple(ll) if ll else None
    return _GEOCODED[key]


def _inside(pt, rings):
    """Ray-casting point-in-polygon over a region's outer rings."""
    x, y = pt
    for ring in rings:
        hit = False
        for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
            if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
                hit = not hit
        if hit:
            return True
    return False


def _km(a, b):
    lat = math.radians((a[1] + b[1]) / 2)
    return math.hypot((a[0] - b[0]) * 111.3 * math.cos(lat), (a[1] - b[1]) * 111.3)


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------

def ease(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


class Camera:
    """Equirectangular around the camera's own latitude: y is scaled by
    1/cos(lat) so coastlines keep their true shape."""
    def __init__(self, lon, lat, width):
        self.lon, self.lat, self.width = lon, lat, width
        self.px_lon = W * SS / width
        self.px_lat = self.px_lon / math.cos(math.radians(lat))

    def xy(self, lon, lat):
        return (W * SS / 2 + (lon - self.lon) * self.px_lon,
                H * SS / 2 - (lat - self.lat) * self.px_lat)


def _lerp_cam(a, b, t):
    e = ease(t)
    return Camera(a[0] + (b[0] - a[0]) * e, a[1] + (b[1] - a[1]) * e,
                  math.exp(math.log(a[2]) + (math.log(b[2]) - math.log(a[2])) * e))


def _cam_at(keys, t):
    if t <= keys[0][0]:
        return Camera(*keys[0][1])
    for (t0, a), (t1, b) in zip(keys, keys[1:]):
        if t <= t1:
            return _lerp_cam(a, b, (t - t0) / max(1e-6, t1 - t0))
    return Camera(*keys[-1][1])


def _pts(cam, ring):
    out, lx, ly = [], None, None
    for x, y in ring:
        px, py = cam.xy(x, y)
        if lx is None or abs(px - lx) + abs(py - ly) >= SS * 1.5:
            out.append((px, py))
            lx, ly = px, py
    return out if len(out) >= 3 else [cam.xy(x, y) for x, y in ring[:3]]


def _bezier(p0, p2, bend=0.3, n=60):
    (x0, y0), (x2, y2) = p0, p2
    x1 = (x0 + x2) / 2 - (y2 - y0) * bend
    y1 = (y0 + y2) / 2 + (x2 - x0) * bend
    return [((1 - t) ** 2 * x0 + 2 * (1 - t) * t * x1 + t * t * x2,
             (1 - t) ** 2 * y0 + 2 * (1 - t) * t * y1 + t * t * y2)
            for t in [i / n for i in range(n + 1)]]


def _arrow(d, pts, frac, width, color, head=True):
    seg = pts[:max(2, int(len(pts) * frac))]
    d.line(seg, fill=color, width=width, joint="curve")
    if head and frac > 0.04:
        (x1, y1), (x2, y2) = seg[-2], seg[-1]
        ang = math.atan2(y2 - y1, x2 - x1)
        L = width * 3.2
        d.polygon([(x2, y2),
                   (x2 + L * math.cos(ang + 2.6), y2 + L * math.sin(ang + 2.6)),
                   (x2 + L * math.cos(ang - 2.6), y2 + L * math.sin(ang - 2.6))],
                  fill=color)


def _crop(rings, bbox):
    lo0, la0, lo1, la1 = bbox
    out = []
    for ring in rings:
        xs = [p[0] for p in ring]
        ys = [p[1] for p in ring]
        if max(xs) >= lo0 and min(xs) <= lo1 and max(ys) >= la0 and min(ys) <= la1:
            out.append(ring)
    return out


def _sea_bend(p0, p1, land):
    """The curve for a movement arrow that crosses the least land: a fleet
    goes round an island, not over it (the first two-map preview drew the
    Seventh Fleet across Taiwan). Tried in lon/-lat, the same orientation
    as the screen, so the bend chosen here is the bend drawn."""
    near = [r for r in land
            if min(_km(q, p0) for q in (r[0], r[len(r) // 2])) < 3000]
    best = None
    for bend in (0.18, -0.18, 0.0, 0.32, -0.32):
        pts = _bezier((p0[0], -p0[1]), (p1[0], -p1[1]), bend=bend, n=24)[2:-2]
        hits = sum(1 for x, y in pts if _inside((x, -y), near))
        if best is None or hits < best[0]:
            best = (hits, bend)
        if hits == 0:
            break
    return best[1]


def render(spec, out_mp4, duration, tone="neutral"):
    """Renders spec (see build_spec) to a silent 1920x1080 clip `duration`
    seconds long. Frames are drawn up to the last animation, then the final
    frame is held, so a long line does not cost minutes of drawing."""
    pal = PALETTES.get(tone, PALETTES["neutral"])
    font = ImageFont.truetype(FONT_PATH, 30 * SS)
    bbox = spec["bbox"]
    land = []
    for f in _load("ne_10m_admin_0_countries"):
        land += _crop(_rings(f["geometry"]), bbox)
    highlights = [(h, _crop(h["rings"], bbox)) for h in spec.get("highlights", [])]
    borders = spec.get("borders", True)

    rnd = random.Random(7)
    arcs = []
    for a in spec.get("arrows", []):
        if a["kind"] == "strike":
            spread = a.get("spread", 0.12)
            for i in range(5):
                arcs.append(((a["from"][0] + rnd.uniform(-spread, spread),
                              a["from"][1] + rnd.uniform(-spread, spread)),
                             (a["to"][0] + rnd.uniform(-spread / 4, spread / 4),
                              a["to"][1] + rnd.uniform(-spread / 6, spread / 6)),
                             a["at"] + i * 0.18, 3, False))
        else:
            arcs.append((a["from"], a["to"], a["at"], 5,
                         _sea_bend(a["from"], a["to"], land)))

    anim_end = min(duration, spec.get("anim_end", duration))
    frames = max(1, int(round(anim_end * FPS)))
    work = tempfile.mkdtemp(prefix="lf_map_")
    try:
        for fi in range(frames):
            t = fi / FPS
            cam = _cam_at(spec["camera"], t)
            img = Image.new("RGB", (W * SS, H * SS), pal["SEA"])
            d = ImageDraw.Draw(img, "RGBA")
            for ring in land:
                d.polygon(_pts(cam, ring), fill=pal["LAND"])
            for h, rings in highlights:
                a = ease((t - h["at"]) / 0.8)
                if a <= 0:
                    continue
                col = pal["SOFT_FILL"] if h["style"] == "soft" else pal["LIT_FILL"]
                for ring in rings:
                    d.polygon(_pts(cam, ring), fill=col + (int(190 * a),))
            if borders:
                for ring in land:
                    p = _pts(cam, ring)
                    d.line(p + [p[0]], fill=pal["LAND_EDGE"], width=SS)
            for h, rings in highlights:
                a = ease((t - h["at"]) / 0.8)
                if a > 0 and h["style"] == "lit":
                    for ring in rings:
                        p = _pts(cam, ring)
                        d.line(p + [p[0]], fill=pal["LIT_EDGE"] + (int(255 * a),),
                               width=2 * SS)

            glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
            gd = ImageDraw.Draw(glow)
            for p0, p1, start, width, bend in arcs:
                move = bend is not False
                frac = ease((t - start) / (1.2 if move else 0.7))
                if frac <= 0:
                    continue
                pts = _bezier(cam.xy(*p0), cam.xy(*p1),
                              bend=bend if move else 0.35)
                _arrow(gd, pts, frac, (width + 7) * SS, RED + (90,), head=False)
                _arrow(d, pts, frac, width * SS, RED + (255,))
            if glow.getbbox():
                small = glow.resize((glow.width // 4, glow.height // 4))
                g = small.filter(ImageFilter.GaussianBlur(1.5 * SS)).resize(
                    glow.size, Image.BILINEAR)
                img.paste(g, (0, 0), g)

            for tg in spec.get("targets", []):
                dt = t - tg["at"]
                if dt < 0:
                    continue
                x, y = cam.xy(*tg["ll"])
                for k in range(2):
                    ph = ((dt + k * 0.6) % 1.2) / 1.2
                    r = (14 + 46 * ph) * SS
                    d.ellipse([x - r, y - r, x + r, y + r],
                              outline=RED + (int(255 * (1 - ph)),), width=3 * SS)
                d.ellipse([x - 7 * SS, y - 7 * SS, x + 7 * SS, y + 7 * SS], fill=RED)

            for lb in spec.get("labels", []):
                dt = t - lb["at"]
                if dt < 0:
                    continue
                n = min(len(lb["text"]), int(dt / 0.055) + 1)
                txt = lb["text"][:n] + ("█" if n < len(lb["text"]) else "")
                x, y = cam.xy(*lb["ll"])
                if lb.get("dot"):
                    d.ellipse([x - 5 * SS, y - 5 * SS, x + 5 * SS, y + 5 * SS],
                              fill=pal["LABEL"])
                tw = d.textlength(lb["text"], font=font)
                off = 26 * SS
                if lb.get("anchor") == "c":
                    tx = x - tw / 2
                else:
                    tx = x + off if lb.get("anchor", "l") == "l" else x - off - tw
                tx = max(40 * SS, min(W * SS - 40 * SS - tw, tx))
                d.text((tx + 2 * SS, y - 15 * SS), txt, font=font, fill=(0, 0, 0, 200))
                d.text((tx, y - 17 * SS), txt, font=font, fill=pal["LABEL"])

            img.resize((W, H), Image.LANCZOS).save(os.path.join(work, f"f{fi:04d}.png"))

        hold = max(0.0, duration - frames / FPS)
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS),
             "-i", os.path.join(work, "f%04d.png"),
             # No vignette here: the final pass adds the video's own.
             "-vf", f"tpad=stop_mode=clone:stop_duration={hold:.3f},"
                    "format=yuv420p",
             "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast",
             "-crf", "18", "-t", f"{duration:.3f}", out_mp4], check=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return out_mp4


# ---------------------------------------------------------------------------
# From a scene to a spec
# ---------------------------------------------------------------------------

def spoken_at(words, name):
    """When `name` starts being spoken in this scene (seconds), or None."""
    want = _norm(name).split()
    if not want or not words:
        return None
    tokens = [_norm(re.sub(r"'s$", "", w.get("word", ""))) for w in words]
    for i in range(len(tokens) - len(want) + 1):
        if tokens[i:i + len(want)] == want:
            return float(words[i].get("start", 0.0))
    for i, tok in enumerate(tokens):             # first word alone
        if tok == want[0]:
            return float(words[i].get("start", 0.0))
    return None


def build_spec(plan, words, duration, era_year=None):
    """plan: {"target", "origin", "action", "light": [...]} (names only).
    Returns a render spec, or None when the target cannot be placed."""
    # placed when the script was written (longform_llm._place_map), so a
    # Wikipedia rate limit at render time cannot cost the map
    target_ll = tuple(plan["ll"]) if plan.get("ll") else geocode(plan["target"])
    if not target_ll:
        print(f"   ↩️  Map skipped: no coordinates for {plan['target']!r}.")
        return None
    if not plan.get("origin") and _is_region(plan["target"]):
        return _region_spec(plan, target_ll, words, duration,
                            modern=not era_year or era_year >= MODERN_BORDERS_FROM)
    # An origin that is a province or country ("from the Fujian coast")
    # strikes from that region's nearest edge to the target — its coast —
    # not from its middle, and is lit softly as where the action came from.
    origin_ll, origin_region = None, None
    if plan.get("origin"):
        region = find_region(plan["origin"])
        if region:
            edge = [p for ring in region[1] for p in ring
                    if _km(p, target_ll) >= 3]
            if edge:
                origin_ll = tuple(min(edge, key=lambda p: _km(p, target_ll))[:2])
                origin_region = (plan["origin"], region[1], region[2])
        else:
            origin_ll = geocode(plan["origin"])
    if origin_ll and not (3 <= _km(origin_ll, target_ll) <= 2500):
        print(f"   ↩️  Map origin {plan['origin']!r} dropped "
              f"({_km(origin_ll, target_ll):.0f} km from the target).")
        origin_ll = None
    modern = not era_year or era_year >= MODERN_BORDERS_FROM

    regions = []
    if modern and origin_region and origin_ll:
        near = [ring for ring in origin_region[1] if _km(ring[0], target_ll) < 1500]
        if near:
            regions.append((origin_region[0], near, origin_region[2], True))
    if modern:
        for i, name in enumerate(plan.get("light") or []):
            if origin_region and _norm(name) == _norm(origin_region[0]):
                continue
            r = find_region(name)
            if not r:
                continue
            near = [ring for ring in r[1]
                    if _km(ring[0], target_ll) < 1500]
            if near:
                regions.append((name, near, r[2], False))

    # Timing, from the narration's own word timings where they exist.
    t_target = spoken_at(words, plan["target"])
    t_target = min(max(t_target if t_target is not None else duration * 0.3, 1.2),
                   max(1.2, duration - 3.0))

    # Cameras: close fits the origin and target; wide is the region around
    # it; final fits the lit regions as well.
    pts = [target_ll] + ([origin_ll] if origin_ll else [])
    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)
    span = max(abs(p[0] - cx) for p in pts) * 2 if len(pts) > 1 else 0
    close_w = max(1.2, span * 3.2)
    close = (cx, cy, close_w)
    wide = (cx, cy, min(40.0, close_w * 9))

    final = None
    if regions:
        xs, ys = [target_ll[0]], [target_ll[1]]
        for _, rings, _, _ in regions:
            for ring in rings:
                xs += [p[0] for p in ring]
                ys += [p[1] for p in ring]
        fx0, fx1, fy0, fy1 = min(xs), max(xs), min(ys), max(ys)
        fw = max(fx1 - fx0, (fy1 - fy0) * (W / H) / math.cos(math.radians(cy))) * 1.3
        final = ((fx0 + fx1) / 2, (fy0 + fy1) / 2, max(close_w * 1.5, min(40.0, fw)))

    if final is None and origin_region:
        # Nothing to light (or before 1900): pull back far enough to show
        # where the move came from, with its name on it.
        ox, oy = origin_region[2]
        xs, ys = [target_ll[0], ox], [target_ll[1], oy]
        fw = max(max(xs) - min(xs),
                 (max(ys) - min(ys)) * (W / H) / math.cos(math.radians(cy))) * 1.6
        final = ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2,
                 max(close_w * 1.5, min(40.0, fw)))

    t_close = max(0.9, t_target - 0.1)
    camera = [(0.0, wide), (t_close, close), (t_target + 1.4, close)]
    events_end = t_target + 2.4
    highlights, labels = [], []
    if final:
        camera.append((t_target + 2.8, final))
        lit_n = 0
        for name, rings, label_ll, is_origin in regions:
            if is_origin or (origin_ll and _inside(origin_ll, rings)):
                # Where the action comes from: lit softly from the start,
                # as the camera pushes in.
                highlights.append({"rings": rings, "style": "soft", "at": 0.3})
                labels.append({"text": name.upper(), "ll": label_ll, "at": 0.5,
                               "anchor": "c"})
                continue
            at = spoken_at(words, name)
            at = max(t_target + 1.6, at if at is not None else 0) + lit_n * 0.4
            lit_n += 1
            highlights.append({"rings": rings, "style": "lit", "at": at})
            labels.append({"text": name.upper(), "ll": label_ll, "at": at + 0.2,
                           "anchor": "c"})
            events_end = max(events_end, at + 0.2 + len(name) * 0.055 + 0.9)
        events_end = max(events_end, t_target + 3.2)

    arrows = []
    action = plan.get("action") or "mark"
    if origin_ll and _km(origin_ll, target_ll) < 25:
        # A region whose edge is almost at the target (Egypt -> the Straits
        # of Tiran) would draw a few-pixel stub: a ring alone says more.
        action = "mark"
    if origin_ll and action in ("strike", "move"):
        spread = min(0.12, close_w * 0.06)
        arrows.append({"from": origin_ll, "to": target_ll, "kind": action,
                       "at": t_target - 0.15, "spread": spread})
        if not origin_region:
            labels.append({"text": plan["origin"].upper(), "ll": origin_ll,
                           "at": max(0.6, t_close - 0.3), "anchor": "r",
                           "dot": True})
        elif not modern:
            # No soft region highlight before 1900 (modern borders), so the
            # region is named at its own label point instead.
            labels.append({"text": plan["origin"].upper(), "ll": origin_region[2],
                           "at": t_target + 2.4, "anchor": "c"})
    labels.append({"text": plan["target"].upper(), "ll": target_ll,
                   "at": t_target + 0.45, "anchor": "l"})

    view = [c for _, c in camera]
    margin = max(c[2] for c in view)
    bbox = (cx - margin, cy - margin * _lat_span(), cx + margin, cy + margin * _lat_span())
    return {
        "bbox": bbox, "camera": camera, "highlights": highlights,
        "arrows": arrows, "targets": [{"ll": target_ll, "at": t_target + 0.45}],
        "labels": labels, "borders": modern,
        "anim_end": min(duration, events_end + 0.6),
    }


def render_map_scene(scene, out_mp4, duration, avoid=None):
    """The map for one scene, or None. The scene carries "map" (names from
    the script step), "words" (narration timings), "era_year" and
    "chapter_role" (its palette)."""
    plan = scene.get("map") or {}
    if not plan.get("target"):
        return None
    try:
        spec = build_spec(plan, scene.get("words") or [], duration,
                          era_year=scene.get("era_year"))
        if not spec:
            return None
        # A second map on (nearly) the same spot adds nothing: skipped.
        target_ll = spec["targets"][0]["ll"]
        if any(_km(target_ll, p) < 50 for p in (avoid or [])):
            print(f"   ↩️  Map on {plan['target']!r} skipped — within 50 km "
                  f"of the video's other map.")
            return None
        tone = TONE_BY_ROLE.get(scene.get("chapter_role"), "neutral")
        render(spec, out_mp4, duration, tone=tone)
        if avoid is not None:
            avoid.append(target_ll)
        print(f"🗺️  Map beat: {plan.get('origin') or '—'} → {plan['target']}"
              f" ({plan.get('action', 'mark')}; lit: "
              f"{', '.join(plan.get('light') or []) or 'none'}).")
        return out_mp4
    except Exception as e:
        print(f"⚠️ Map beat skipped ({type(e).__name__}: {e}) — keeping the "
              f"scene's photos.")
        return None
