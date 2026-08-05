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


def _polite_get(url, **kwargs):
    """A GET that respects the archives' rate limits: serialised, identified,
    and backing off when told to."""
    global _last_request_at
    kwargs.setdefault("headers", HEADERS)
    kwargs.setdefault("timeout", 60)
    for attempt, backoff in enumerate((0,) + RATE_LIMIT_BACKOFF):
        if backoff:
            time.sleep(backoff)
        gap = time.time() - _last_request_at
        if gap < DOWNLOAD_GAP_SECONDS:
            time.sleep(DOWNLOAD_GAP_SECONDS - gap)
        _last_request_at = time.time()
        response = requests.get(url, **kwargs)
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


def download_archival_image(candidate: dict, output_path: str,
                            width: int, height: int) -> bool:
    """Downloads one Commons candidate and saves it fitted to width x height."""
    try:
        r = _polite_get(candidate["url"])
        r.raise_for_status()
        if len(r.content) > 40 * 1024 * 1024:
            return False
        img = Image.open(io.BytesIO(r.content)).convert("RGB")
    except Exception as e:
        print(f"⚠️ Archival download failed ({candidate.get('title', '?')}): {e}")
        return False
    _fit_frame(img, width, height).save(output_path, "JPEG", quality=92)
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
