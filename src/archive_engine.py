"""
Archive engine — REAL imagery for a channel about real events.

An AI illustration of the Budapest Memorandum is a guess; the actual photograph
of the signing table is evidence. For a history channel the archival photo wins
every time it exists, so this engine is tried before Pollinations, not after.

Two sources, both free:

1. Wikimedia Commons (no API key). The largest archive of public-domain and
   freely licensed historical photography on the internet, and its coverage is
   densest exactly where this channel lives: ships, battles, treaties, disasters,
   dead politicians. Every image used is credited in the video description,
   which CC-BY licences require and public-domain courtesy suggests.

2. Pexels video (optional, free API key in PEXELS_API_KEY). Muted, slow stock
   footage — storm seas, archive shelves, old machinery — used as connective
   tissue for segments with no photographable subject. If the key is absent the
   pipeline silently skips video and nothing breaks.

Junk control matters more than recall here: Commons will return SOMETHING for
almost any query, and an irrelevant real photo reads worse than a good AI frame.
Candidates are filtered (no flags, no coats of arms, no locator maps, nothing
under 900px) and scored before anything is accepted.
"""

import io
import os
import re
import subprocess
import time
import urllib.parse

import requests
from PIL import Image, ImageEnhance, ImageFilter

COMMONS_API = "https://commons.wikimedia.org/w/api.php"
LOC_API = "https://www.loc.gov/photos/"

# Wikimedia's User-Agent policy wants a real contact URL, and enforces it: a
# vague UA plus back-to-back downloads earned a run full of
# "429 ... does not comply with our robot policy". This UA identifies the bot
# and points at the repository, which is what the policy asks for.
HEADERS = {
    "User-Agent": ("ArchiveParadoxBot/1.1 "
                   "(https://github.com/BagiTheKing/shadow-archives-bot; "
                   "automated documentary pipeline)"),
    "Accept": "image/jpeg,image/png,image/*;q=0.8",
}

# Wikimedia asks well-behaved clients to serialise their requests. The first
# live run fired ten image downloads with no pause and was rate-limited off the
# service for the rest of the video; one request at a time with a short gap
# costs a couple of minutes and actually returns pictures.
DOWNLOAD_GAP_SECONDS = 1.2
_last_request_at = 0.0

# How long to wait after a 429 before trying the next candidate.
RATE_LIMIT_BACKOFF = (4, 9, 16)


# The owner's Wikimedia access token (WIKIMEDIA_TOKEN): requests made as a
# logged-in user get far higher limits than anonymous ones, which ran out
# while researching and placing maps. Sent to the wiki APIs only, not to
# image downloads, and dropped for the run if Wikimedia ever refuses it.
_WIKI_HOSTS = ("wikipedia.org", "wikimedia.org", "wikiquote.org", "wikidata.org")
_wiki_token_ok = True


def wiki_auth(url, headers=None):
    """`headers` plus the Wikimedia token when `url` is a wiki API."""
    headers = dict(headers or {})
    token = os.getenv("WIKIMEDIA_TOKEN", "").strip()
    host = urllib.parse.urlparse(url).netloc
    if (token and _wiki_token_ok and host.endswith(_WIKI_HOSTS)
            and not host.startswith("upload.")):
        headers["Authorization"] = f"Bearer {token}"
    return headers


def wiki_token_refused(status):
    """Called with a wiki API's status: a refused token is not sent again."""
    global _wiki_token_ok
    if status in (401, 403) and _wiki_token_ok and os.getenv("WIKIMEDIA_TOKEN"):
        _wiki_token_ok = False
        print("⚠️ Wikimedia refused the access token — continuing anonymously.")
        return True
    return False


def _polite_get(url, **kwargs):
    """A GET that respects the archives' rate limits: serialised, identified,
    and backing off when told to."""
    global _last_request_at
    base_headers = kwargs.pop("headers", HEADERS)
    kwargs.setdefault("timeout", 60)
    for attempt, backoff in enumerate((0,) + RATE_LIMIT_BACKOFF):
        if backoff:
            time.sleep(backoff)
        gap = time.time() - _last_request_at
        if gap < DOWNLOAD_GAP_SECONDS:
            time.sleep(DOWNLOAD_GAP_SECONDS - gap)
        _last_request_at = time.time()
        response = requests.get(url, headers=wiki_auth(url, base_headers), **kwargs)
        if wiki_token_refused(response.status_code):
            response = requests.get(url, headers=wiki_auth(url, base_headers), **kwargs)
        if response.status_code != 429:
            return response
    return response

PEXELS_VIDEO_API = "https://api.pexels.com/videos/search"

# Below this width a photo turns to soup when Ken Burns pushes into it.
MIN_SOURCE_WIDTH = 900

# Files that are technically images of the subject but never usable as a
# documentary frame. Matched against the lowercased Commons file title.
JUNK_TITLE_WORDS = (
    "flag of", "coat of arms", "coat_of_arms", "logo", "seal of", "locator",
    "location map", "map of", "banner", "icon", "escudo", "wappen", "emblem",
    "roundel", "insignia", "signature", "stamp of", "postage stamp",
    "chart", "diagram", "graph",
)

USABLE_MIMES = ("image/jpeg", "image/png", "image/tiff")

# A frame this far from 16:9 gets the blur-pad treatment instead of a crop that
# would behead the subject.
CROP_RATIO_MIN, CROP_RATIO_MAX = 1.25, 2.6

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    """Commons returns Artist as an HTML fragment ('<a href=...>Yoichi Okamoto</a>')."""
    return _TAG_RE.sub("", text or "").replace("\n", " ").strip()


def _candidate_score(info: dict, title: str):
    """Ranks one Commons imageinfo dict. Returns None for unusable files."""
    low = title.lower().replace("_", " ")
    for junk in JUNK_TITLE_WORDS:
        if junk in low:
            return None
    width, height = info.get("width", 0), info.get("height", 0)
    if width < MIN_SOURCE_WIDTH or height < 500:
        return None
    if info.get("mime") not in USABLE_MIMES:
        return None
    ratio = width / height
    if not (0.5 <= ratio <= 3.5):
        return None
    score = min(width, 2600)
    # Landscape drops straight into a 16:9 frame; portrait costs a blur-pad.
    if CROP_RATIO_MIN <= ratio <= CROP_RATIO_MAX:
        score += 900
    return score


def search_commons(query: str, limit: int = 10) -> list:
    """Returns usable candidates for a query, best first. Each candidate:
    {url, width, height, title, artist, license}. Never raises — a dead
    network returns [] and the caller falls back to generation."""
    params = {
        "action": "query", "format": "json", "generator": "search",
        "gsrsearch": f"filetype:bitmap {query}", "gsrnamespace": 6,
        "gsrlimit": limit, "prop": "imageinfo",
        "iiprop": "url|size|mime|extmetadata",
        # Ask the server for a 1600px-wide derivative so a 90 MB scan of an
        # 1890s plate arrives as a sub-MB JPEG. Above the 1440px the pipeline
        # actually needs, bigger only buys rate-limiting.
        "iiurlwidth": 1600,
    }
    try:
        r = _polite_get(COMMONS_API, params=params, timeout=25)
        r.raise_for_status()
        pages = r.json().get("query", {}).get("pages", {})
    except Exception as e:
        print(f"⚠️ Commons search failed for {query!r}: {e}")
        return []

    return _candidates(pages)


def _candidates(pages: dict) -> list:
    """Usable Commons pages (with imageinfo) as candidates, best first."""
    scored = []
    for page in pages.values():
        infos = page.get("imageinfo") or []
        if not infos:
            continue
        info = infos[0]
        title = page.get("title", "")
        score = _candidate_score(info, title)
        if score is None:
            continue
        meta = info.get("extmetadata") or {}
        scored.append((score, {
            "url": info.get("thumburl") or info.get("url"),
            "width": info.get("width"), "height": info.get("height"),
            "title": title.replace("File:", "").rsplit(".", 1)[0],
            "artist": _strip_html((meta.get("Artist") or {}).get("value", "")),
            "license": _strip_html(
                (meta.get("LicenseShortName") or {}).get("value", "")),
        }))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [c for _, c in scored]


def article_lead_image(article_title: str):
    """The free lead image of a Wikipedia article, as a Commons candidate, or
    None. The lead image is the one picture an editor chose to stand for the
    whole event, which makes it the most reliably relevant real image there
    is. pilicense=free keeps out the fair-use files English Wikipedia hosts
    itself, which this channel may not reuse."""
    try:
        r = _polite_get("https://en.wikipedia.org/w/api.php", params={
            "action": "query", "format": "json", "titles": article_title,
            "prop": "pageimages", "piprop": "name", "pilicense": "free",
            "redirects": 1}, timeout=25)
        r.raise_for_status()
        pages = (r.json().get("query") or {}).get("pages") or {}
        name = next((p.get("pageimage") for p in pages.values()
                     if p.get("pageimage")), None)
        if not name:
            return None
        r = _polite_get(COMMONS_API, params={
            "action": "query", "format": "json", "titles": f"File:{name}",
            "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata",
            "iiurlwidth": 1600}, timeout=25)
        r.raise_for_status()
        found = _candidates((r.json().get("query") or {}).get("pages") or {})
    except Exception as e:
        print(f"⚠️ Lead image lookup failed for {article_title!r}: {e}")
        return None
    return found[0] if found else None


def _fit_frame(img: Image.Image, w: int, h: int) -> Image.Image:
    """Fills w x h. Near-16:9 sources get a centred cover crop; everything else
    (portraits of people, tall paintings) sits over a blurred, darkened
    enlargement of itself — the standard documentary treatment, and it never
    amputates the subject."""
    ratio = img.width / img.height
    target = w / h
    if CROP_RATIO_MIN <= ratio <= CROP_RATIO_MAX:
        if ratio > target:
            new_w = int(img.height * target)
            x = (img.width - new_w) // 2
            img = img.crop((x, 0, x + new_w, img.height))
        else:
            new_h = int(img.width / target)
            y = (img.height - new_h) // 2
            img = img.crop((0, y, img.width, y + new_h))
        return img.resize((w, h), Image.LANCZOS)

    # Blur-pad path.
    bg = img.copy()
    scale = max(w / bg.width, h / bg.height)
    bg = bg.resize((int(bg.width * scale) + 1, int(bg.height * scale) + 1),
                   Image.LANCZOS)
    bg = bg.crop(((bg.width - w) // 2, (bg.height - h) // 2,
                  (bg.width - w) // 2 + w, (bg.height - h) // 2 + h))
    bg = bg.filter(ImageFilter.GaussianBlur(w // 30))
    bg = ImageEnhance.Brightness(bg).enhance(0.45)

    fg_scale = min(w / img.width, h / img.height)
    fg = img.resize((int(img.width * fg_scale), int(img.height * fg_scale)),
                    Image.LANCZOS)
    bg.paste(fg, ((w - fg.width) // 2, (h - fg.height) // 2))
    return bg


def fit_vertical(img: Image.Image, w: int, h: int) -> Image.Image:
    """Fills a tall w x h frame (the Shorts' 9:16). Cropping a landscape photo
    to 9:16 keeps a third of it, so anything wider than tall is cut to at most
    a square and sits across the full width over a blurred, darkened copy of
    itself; a tall photo fills the width; only a photo taller than the frame
    is cover-cropped."""
    ratio = img.width / img.height
    if ratio <= w / h:
        new_h = int(img.width * h / w)
        y = max(0, (img.height - new_h) // 3)      # keep the top: faces, masts
        return img.crop((0, y, img.width, y + new_h)).resize((w, h), Image.LANCZOS)

    fg = img
    if ratio > 1.0:
        side = img.height
        x = (img.width - side) // 2
        fg = img.crop((x, 0, x + side, side))
    fg = fg.resize((w, int(w * fg.height / fg.width)), Image.LANCZOS)

    scale = max(w / img.width, h / img.height)
    bg = img.resize((int(img.width * scale) + 1, int(img.height * scale) + 1),
                    Image.LANCZOS)
    bg = bg.crop(((bg.width - w) // 2, (bg.height - h) // 2,
                  (bg.width - w) // 2 + w, (bg.height - h) // 2 + h))
    bg = bg.filter(ImageFilter.GaussianBlur(w // 25))
    bg = ImageEnhance.Brightness(bg).enhance(0.4)
    bg.paste(fg, (0, (h - fg.height) // 2))
    return bg


def download_archival_image(candidate: dict, output_path: str,
                            width: int, height: int, fit=None) -> bool:
    """Downloads one Commons candidate and saves it fitted to width x height
    (by `fit`, the 16:9 treatment unless told otherwise)."""
    try:
        r = _polite_get(candidate["url"])
        r.raise_for_status()
        if len(r.content) > 40 * 1024 * 1024:
            return False
        img = Image.open(io.BytesIO(r.content)).convert("RGB")
    except Exception as e:
        print(f"⚠️ Archival download failed ({candidate.get('title', '?')}): {e}")
        return False
    (fit or _fit_frame)(img, width, height).save(output_path, "JPEG", quality=92)
    return True


def search_loc(query: str, limit: int = 8) -> list:
    """The Library of Congress photo archive — free, no key, and far deeper
    than Commons on American military and political history, which is most of
    this channel. Returns candidates in the same shape as search_commons.

    One Commons search per scene was leaving whole chapters with nothing real
    to show; a second independent archive roughly doubles the hit rate and
    means a rate-limited Wikimedia no longer empties the video."""
    try:
        r = _polite_get(LOC_API, params={"q": query, "fo": "json", "c": limit,
                                         "at": "results"}, timeout=25)
        r.raise_for_status()
        results = (r.json() or {}).get("results", []) or []
    except Exception as e:
        print(f"⚠️ Library of Congress search failed for {query!r}: {e}")
        return []

    out = []
    for item in results:
        urls = item.get("image_url") or []
        if not isinstance(urls, list) or not urls:
            continue
        url = urls[-1]                      # last entry is the largest render
        if not isinstance(url, str) or not url.startswith("http"):
            continue
        title = item.get("title") or "Library of Congress photograph"
        low = title.lower()
        if any(j in low for j in JUNK_TITLE_WORDS):
            continue
        out.append({
            "url": url, "width": None, "height": None,
            "title": str(title)[:120],
            "artist": "", "license": "Library of Congress",
            "source": "Library of Congress",
        })
    return out


def search_archives(query: str, limit: int = 10) -> list:
    """Every free archive we have, best candidates first.

    Commons leads because its metadata is richer and its licensing explicit;
    the Library of Congress follows and covers the gaps — including whenever
    Wikimedia is rate-limiting us."""
    results = search_commons(query, limit=limit)
    if len(results) < 3:
        results += search_loc(query)
    return results


def format_credit(candidate: dict) -> str:
    parts = [candidate.get("title", "").strip() or "Untitled"]
    if candidate.get("artist"):
        parts.append(candidate["artist"][:60])
    if candidate.get("license"):
        parts.append(candidate["license"])
    source = candidate.get("source") or "Wikimedia Commons"
    return " — ".join(parts) + f" (via {source})"


# ---------------------------------------------------------------------------
# Pexels muted b-roll (optional)
# ---------------------------------------------------------------------------

def pexels_available() -> bool:
    return bool(os.getenv("PEXELS_API_KEY"))


def _pick_video_file(video: dict):
    """Smallest MP4 rendition that still covers 1080p; failing that, the
    largest available. Downloading 4K to crush it to 1080p wastes the run's
    bandwidth and disk."""
    files = [f for f in video.get("video_files", [])
             if f.get("link") and f.get("width") and f.get("height")]
    if not files:
        return None
    hd = [f for f in files if f["height"] >= 1080 and f["width"] >= 1920]
    if hd:
        return min(hd, key=lambda f: f["width"] * f["height"])
    return max(files, key=lambda f: f["width"] * f["height"])


def fetch_broll_video(query: str, output_path: str,
                      min_duration: float = 5.0) -> bool:
    """Downloads one muted landscape stock clip for the query. Returns False on
    any failure — b-roll is a garnish, never worth failing a run over."""
    key = os.getenv("PEXELS_API_KEY")
    if not key:
        return False
    try:
        r = requests.get(PEXELS_VIDEO_API,
                         params={"query": query, "per_page": 6,
                                 "orientation": "landscape", "size": "medium"},
                         headers={"Authorization": key}, timeout=25)
        r.raise_for_status()
        videos = r.json().get("videos", [])
    except Exception as e:
        print(f"⚠️ Pexels search failed for {query!r}: {e}")
        return False

    for video in videos:
        if video.get("duration", 0) < min_duration:
            continue
        file = _pick_video_file(video)
        if not file:
            continue
        try:
            with requests.get(file["link"], timeout=120, stream=True) as resp:
                resp.raise_for_status()
                size = 0
                with open(output_path, "wb") as f:
                    for chunk in resp.iter_content(1 << 20):
                        size += len(chunk)
                        if size > 120 * 1024 * 1024:
                            raise ValueError("clip too large")
                        f.write(chunk)
            return True
        except Exception as e:
            print(f"⚠️ Pexels download failed: {e}")
            if os.path.exists(output_path):
                os.remove(output_path)
    return False


def extract_poster_frame(video_path: str, output_path: str,
                         width: int, height: int) -> bool:
    """First usable frame of a clip, fitted to the working size. Gives video
    scenes an image_path so thumbnails and fallbacks keep working."""
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-ss", "0.5", "-i", video_path,
             "-frames:v", "1", "-q:v", "3", output_path],
            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, check=True)
        img = Image.open(output_path).convert("RGB")
        _fit_frame(img, width, height).save(output_path, "JPEG", quality=90)
        return True
    except Exception as e:
        print(f"⚠️ Poster frame extraction failed: {e}")
        return False


# ---------------------------------------------------------------------------
# Real archival video — Internet Archive, trusted collections only
#
# archive.org hosts a huge amount of genuine historical footage, but it is a
# general-purpose upload site: plenty of what is hosted there is user-
# uploaded with murky or simply wrong rights claims, "public domain" in the
# item description notwithstanding. That is not a hypothetical risk for this
# channel specifically — the exact same failure mode is already documented
# for the CC-BY music tracks in music/README.md, and a Content ID claim on a
# restoration of otherwise-PD footage does not care that the underlying
# 80-year-old footage itself is free.
#
# So this does not search archive.org generally. It only searches inside
# TRUSTED_VIDEO_COLLECTIONS — collections whose public-domain status is a
# deliberate, documented fact about the collection as a whole, not a
# per-uploader claim:
#
#   universal_newsreels   Universal City Studios produced newsreels from
#                         1929-1967 and explicitly dedicated the entire
#                         library to the public domain, donating the
#                         physical materials to the National Archives in
#                         1976. Confirmed across multiple independent
#                         sources describing the same donation, not one
#                         page's say-so.
#
# Add to this list only with the same bar: a specific, citable reason the
# WHOLE collection is free, not "it's old" or "it's on archive.org".
TRUSTED_VIDEO_COLLECTIONS = ["universal_newsreels"]

ARCHIVE_ORG_SEARCH_API = "https://archive.org/advancedsearch.php"
ARCHIVE_ORG_METADATA_API = "https://archive.org/metadata"

# A newsreel item is very often a compiled reel of six or seven unrelated
# stories, not one continuous piece about one subject — there is no reliable
# way to know which few seconds of a long item actually match the search
# query without per-segment timestamps, which this metadata does not carry.
# Rather than guess and risk showing the wrong story under the right
# narration, long items are skipped entirely; short, single-subject items are
# the only ones actually used.
MAX_SOURCE_RUNTIME_SECONDS = 300

# Regardless of the source item's own length, only ever use this many seconds
# of it — a firm cap, not a hint, enforced by ffmpeg at extraction time. Keeps
# every clip roughly the length of the scene it illustrates and guarantees
# real footage never dominates a video the way a single long take would.
ARCHIVAL_CLIP_SECONDS = 18

# A few seconds in, past the title card most newsreel items open on.
ARCHIVAL_CLIP_START_OFFSET = 2.0


def _parse_runtime(value) -> float | None:
    """Internet Archive's 'runtime' metadata field is inconsistent — usually
    "H:MM:SS" or "MM:SS", occasionally a bare number of seconds, sometimes
    missing. Returns None for anything that cannot be read as either shape,
    which callers treat as "unknown", not "short"."""
    if not value:
        return None
    text = str(value).strip()
    if re.match(r"^\d+(\.\d+)?$", text):
        return float(text)
    parts = text.split(":")
    if 2 <= len(parts) <= 3 and all(re.match(r"^\d+(\.\d+)?$", p) for p in parts):
        seconds = 0.0
        for p in parts:
            seconds = seconds * 60 + float(p)
        return seconds
    return None


def search_archival_video(query: str, limit: int = 5) -> list:
    """Real historical footage from TRUSTED_VIDEO_COLLECTIONS only — see the
    section comment above for why this is not a general archive.org search.

    Returns [{identifier, title}, ...], long or duration-unknown items
    already excluded.
    """
    collections = " OR ".join(f"collection:{c}" for c in TRUSTED_VIDEO_COLLECTIONS)
    q = f"({collections}) AND mediatype:(movies) AND ({query})"
    try:
        r = _polite_get(ARCHIVE_ORG_SEARCH_API, params={
            "q": q,
            "fl[]": ["identifier", "title", "runtime"],
            "rows": limit, "output": "json"})
        r.raise_for_status()
        docs = (r.json() or {}).get("response", {}).get("docs", []) or []
    except Exception as e:
        print(f"⚠️ Internet Archive search failed for {query!r}: {e}")
        return []

    out = []
    for d in docs:
        identifier = d.get("identifier")
        if not identifier:
            continue
        runtime = _parse_runtime(d.get("runtime"))
        # Unknown runtime is excluded too, not just long runtime: a
        # compilation reel with missing metadata is exactly as risky as one
        # that openly states it is 45 minutes long.
        if runtime is None or runtime > MAX_SOURCE_RUNTIME_SECONDS:
            continue
        out.append({"identifier": identifier,
                    "title": str(d.get("title") or identifier)[:120]})
    return out


def _pick_archive_org_file(identifier: str):
    """The item's own metadata lists every file it contains; picks the first
    usable MP4 rendition (archive.org typically offers more than one:
    original upload plus a re-encoded 512kb/h.264 derivative)."""
    try:
        r = _polite_get(f"{ARCHIVE_ORG_METADATA_API}/{identifier}")
        r.raise_for_status()
        files = (r.json() or {}).get("files", []) or []
    except Exception as e:
        print(f"⚠️ Could not read Internet Archive metadata for "
              f"{identifier!r}: {e}")
        return None
    mp4s = [f for f in files
           if str(f.get("name", "")).lower().endswith(".mp4")]
    if not mp4s:
        return None
    # Prefer a named "512kb" derivative when present — small enough to not
    # waste the run's bandwidth on footage only 18 seconds of it gets used.
    preferred = [f for f in mp4s if "512kb" in f.get("name", "").lower()]
    chosen = (preferred or mp4s)[0]
    return chosen.get("name")


def fetch_archival_video(identifier: str, output_path: str) -> bool:
    """Downloads a short, fixed-length clip from a trusted-collection item —
    never the whole file. Returns False on any failure; real archival footage
    is a bonus over a still image, never worth failing a run over."""
    filename = _pick_archive_org_file(identifier)
    if not filename:
        return False

    url = f"https://archive.org/download/{identifier}/{filename}"
    tmp_path = output_path + ".full.mp4"
    try:
        with _polite_get(url, stream=True, timeout=120) as resp:
            resp.raise_for_status()
            size = 0
            with open(tmp_path, "wb") as f:
                for chunk in resp.iter_content(1 << 20):
                    size += len(chunk)
                    if size > 150 * 1024 * 1024:
                        raise ValueError("source file too large")
                    f.write(chunk)
    except Exception as e:
        print(f"⚠️ Internet Archive download failed ({identifier}): {e}")
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        return False

    try:
        subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
             "-ss", f"{ARCHIVAL_CLIP_START_OFFSET}", "-i", tmp_path,
             "-t", f"{ARCHIVAL_CLIP_SECONDS}", "-an",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
             output_path],
            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, check=True)
        ok = os.path.exists(output_path) and os.path.getsize(output_path) > 0
        if ok and is_static(output_path):
            os.remove(output_path)
            return False
        return ok
    except Exception as e:
        print(f"⚠️ Could not trim Internet Archive clip ({identifier}): {e}")
        return False
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def format_archival_video_credit(identifier: str, title: str) -> str:
    return (f"{title} (Internet Archive — {identifier}, public domain, "
           f"via archive.org)")


# ---------------------------------------------------------------------------
# Real archival video — Wikimedia Commons
#
# The newsreel collection above stops in 1967 and its runtime rules leave very
# little, so in practice no run ever showed a moving picture. Commons hosts a
# great deal of genuine historical footage (government newsreels, army film,
# speeches), every file carrying its own free licence and author, which is
# the same bar the stills already meet. Files are streamed and cut by ffmpeg
# straight from Commons' own transcodes: only the seconds used are fetched.
# ---------------------------------------------------------------------------

# Modern productions about the past, not footage of it.
VIDEO_JUNK_WORDS = ("voice recording", "announcement", "audio", "interview", "documentary", "trailer", "lecture", "animation",
                    "animated", "game", "tutorial", "podcast", "wikipedia",
                    "slideshow", "timelapse", "time-lapse", "reenactment",
                    "re-enactment", "museum", "memorial", "anniversary",
                    "ceremony 20", "vlog", "tour")
VIDEO_MIN_SECONDS = 8
VIDEO_MAX_SECONDS = 3600
_YEAR_RE = re.compile(r"\b(1[89]\d\d|20[0-2]\d)\b")


def _video_year(title: str, meta: dict):
    """The year the footage was shot: the file's own original date first,
    then a year in its title. None when neither says."""
    m = _YEAR_RE.search(_strip_html(
        (meta.get("DateTimeOriginal") or {}).get("value", "")))
    if not m:
        m = _YEAR_RE.search(title)
    return int(m.group(1)) if m else None


def _best_derivative(info: dict):
    """Commons re-encodes every video; the 480p or 720p WebM is plenty for a
    scene that is graded and cover-cropped anyway, and far smaller."""
    ranked = []
    for d in info.get("derivatives") or []:
        src, h = d.get("src"), int(d.get("height") or 0)
        if not src or h < 240:
            continue
        ranked.append((abs(h - 600), src))
    ranked.sort()
    url = ranked[0][1] if ranked else info.get("url")
    return f"https:{url}" if url and url.startswith("//") else url


def search_commons_video(query: str, era=None, limit: int = 12) -> list:
    """Genuine footage from Wikimedia Commons for a query. Each candidate:
    {url, title, artist, license, duration, year, source}. Footage dated
    after the story's era (a modern documentary, a commemoration) is dropped,
    and so is undated footage when the era is known: a wrong clip under the
    right narration is worse than a still."""
    params = {
        "action": "query", "format": "json", "generator": "search",
        "gsrsearch": f"filetype:video {query}", "gsrnamespace": 6,
        "gsrlimit": limit, "prop": "videoinfo",
        "viprop": "url|size|mime|extmetadata|derivatives",
    }
    try:
        r = _polite_get(COMMONS_API, params=params, timeout=25)
        r.raise_for_status()
        pages = r.json().get("query", {}).get("pages", {})
    except Exception as e:
        print(f"⚠️ Commons video search failed for {query!r}: {e}")
        return []

    out = []
    for page in sorted(pages.values(), key=lambda p: p.get("index", 99)):
        infos = page.get("videoinfo") or []
        if not infos:
            continue
        info = infos[0]
        title = page.get("title", "").replace("File:", "").rsplit(".", 1)[0]
        low = title.lower().replace("_", " ")
        if any(w in low for w in VIDEO_JUNK_WORDS):
            continue
        duration = float(info.get("duration") or 0)
        if not (VIDEO_MIN_SECONDS <= duration <= VIDEO_MAX_SECONDS):
            continue
        if int(info.get("width") or 0) < 320:
            continue
        meta = info.get("extmetadata") or {}
        year = _video_year(title, meta)
        if era:
            if year is None or year > era + 3 or year < era - 40:
                continue
        url = _best_derivative(info)
        if not url:
            continue
        about = " ".join([title.replace("_", " "),
                          _strip_html((meta.get("ImageDescription") or {}).get("value", "")),
                          _strip_html((meta.get("Categories") or {}).get("value", ""))])
        out.append({
            "url": url, "title": title.replace("_", " ")[:120],
            "about": about.lower()[:3000],
            "tagline": " ".join([title.replace("_", " "), _strip_html(
                (meta.get("Categories") or {}).get("value", ""))]).lower(),
            "artist": _strip_html((meta.get("Artist") or {}).get("value", ""))[:60],
            "license": _strip_html(
                (meta.get("LicenseShortName") or {}).get("value", "")),
            "duration": duration, "year": year, "source": "Wikimedia Commons",
        })
    # Closest to the story's year first: for a 1967 story, Nasser in 1967
    # before Nasser in 1958. Search rank breaks ties (sort is stable).
    if era:
        out.sort(key=lambda c: abs((c["year"] or era) - era))
    return out


def _clip_seconds(path):
    """Length of a cut clip, 0 when it cannot be read."""
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                              "format=duration", "-of", "csv=p=0", path],
                             capture_output=True, text=True, timeout=30)
        return float(out.stdout.strip() or 0)
    except Exception:
        return 0.0


_FOOTAGE_FILLER = {"footage", "film", "newsreel", "photo", "scene", "video",
                   "archive", "archival", "historic", "historical"}


def footage_matches(query: str, about: str) -> bool:
    """Whether a clip is really OF the scene's subject: its title,
    description and categories must carry the query's words (matched on
    their first five letters, so "Egyptian" finds "Egypt"). All of them for
    a one- or two-word query, all but one for a longer one. Years and
    filler words do not count. The first live run put Nasser's resignation
    speech under "Egyptian radar station": any 1967 clip passed."""
    words = [w for w in re.findall(r"[a-z]+", (query or "").lower())
             if len(w) >= 4 and w not in _FOOTAGE_FILLER]
    if not words:
        return False
    hits = sum(1 for w in words if w[:5] in (about or ""))
    return hits >= (len(words) if len(words) <= 2 else len(words) - 1)


def is_static(path) -> bool:
    """True for a "video" that is really still photographs (a slideshow over
    an audio recording, like the general's announcement in the Six-Day War
    run): almost no frame differs from the one before it."""
    try:
        import numpy as np
        raw = subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-i", path, "-vf",
             "fps=4,scale=64:36,format=gray", "-f", "rawvideo", "-"],
            capture_output=True, timeout=120).stdout
        n = len(raw) // (64 * 36)
        if n < 6:
            return False
        f = np.frombuffer(raw[: n * 64 * 36], np.uint8).reshape(n, 36, 64).astype(np.float32)
        diffs = np.abs(np.diff(f, axis=0)).mean(axis=(1, 2))
        return float(np.percentile(diffs, 70)) < 1.2
    except Exception:
        return False


def fetch_commons_video(candidate: dict, output_path: str,
                        seconds: float = ARCHIVAL_CLIP_SECONDS, start=None) -> bool:
    """Cuts `seconds` of silent footage out of a Commons video. Streams
    straight from Commons first, so only the seconds used are fetched; if
    that fails (some transcodes do not seek over HTTP) the file is downloaded
    once, capped, and cut locally. Short files start just past their
    opening; long ones (a full newsreel, a whole speech) a fifth of the way
    in, clear of title cards and leader."""
    dur = candidate.get("duration") or 0
    if start is None:
        start = 1.0 if dur <= seconds + 4 else min(dur * 0.2, dur - seconds - 2)
    length = min(seconds, max(4.0, dur - 1 - start))

    def cut(src, extra=()):
        r = subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *extra,
             "-ss", f"{max(0.0, start):.2f}", "-i", src,
             "-t", f"{length:.2f}", "-an",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
             "-pix_fmt", "yuv420p", output_path],
            capture_output=True, text=True, timeout=240)
        ok = r.returncode == 0 and _clip_seconds(output_path) >= min(4.0, length - 1)
        if not ok:
            print(f"⚠️ Commons video cut failed ({candidate.get('title')}): "
                  f"{(r.stderr or 'clip too short').strip()[-160:]}")
        return ok

    ok = False
    try:
        ok = cut(candidate["url"], ("-user_agent", HEADERS["User-Agent"],
                                    "-rw_timeout", "60000000"))
    except Exception as e:
        print(f"⚠️ Commons video stream failed ({candidate.get('title')}): {e}")
    if not ok:
        tmp = output_path + ".src"
        try:
            with _polite_get(candidate["url"], stream=True, timeout=120,
                             headers={**HEADERS, "Accept": "*/*"}) as resp:
                resp.raise_for_status()
                size = 0
                with open(tmp, "wb") as f:
                    for chunk in resp.iter_content(1 << 20):
                        size += len(chunk)
                        if size > 150 * 1024 * 1024:
                            raise ValueError("source file too large")
                        f.write(chunk)
            ok = cut(tmp)
        except Exception as e:
            print(f"⚠️ Commons video download failed ({candidate.get('title')}): {e}")
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
    if ok and is_static(output_path):
        print(f"   ↩️  {candidate.get('title')!r}: still photographs, not footage — skipped.")
        candidate["_static"] = True
        ok = False
    if not ok and os.path.exists(output_path):
        os.remove(output_path)
    return ok
