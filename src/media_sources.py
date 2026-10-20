"""
Two more free archives of real pictures (owner, 2026-10-20), searched after
Wikimedia Commons and the Library of Congress and before any generated image.
A Houthi teaser found nothing real for Yemen in the first two and fell back
to stock fog and dunes.

1. DVIDS (DVIDS_API_KEY, the account's public "key-..." key): the US
   Department of Defense's public media library, photos and video, free for
   commercial use, credited to DVIDS. Its coverage is where this channel
   lives: the Red Sea, Yemen, Iraq, Syria, the Gulf, ships and strikes.
2. Openverse (no key): openly licensed photos from Flickr and others, only
   licences that allow commercial use and modification, each credited.

Both return candidates in archive_engine's shape ({"url", "title", "year",
"width", "height", "artist", "license", "source"}), so the existing filters
and download_archival_image take them as they are. Any failure returns [].
"""

import os
import re

import requests

DVIDS_API = "https://api.dvidshub.net"
OPENVERSE_API = "https://api.openverse.org/v1/images/"
HEADERS = {"User-Agent": "ArchiveParadoxBot/1.1 (https://github.com/BagiTheKing/shadow-archives-bot)"}


def dvids_available() -> bool:
    return bool(os.getenv("DVIDS_API_KEY"))


def _year(text):
    m = re.search(r"\b(19|20)\d{2}\b", str(text or ""))
    return int(m.group(0)) if m else None


def _get(url, params, timeout=25):
    r = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.json()


def search_dvids(query: str, kind: str = "image", limit: int = 12, raw=None) -> list:
    """DVIDS photos (kind="image") or videos (kind="video") for a query.
    A video candidate's url is its largest mp4 and carries "duration"."""
    if not dvids_available():
        return []
    key = os.environ["DVIDS_API_KEY"]
    try:
        found = _get(f"{DVIDS_API}/search", {"api_key": key, "q": query,
                                             "type[]": kind, "max_results": limit})
    except Exception as e:
        print(f"⚠️ DVIDS search failed ({type(e).__name__}: {e}).")
        return []
    if raw is not None:
        raw.append({"search": found})
    out = []
    for hit in (found.get("results") or [])[:limit]:
        asset_id = hit.get("id")
        if not asset_id:
            continue
        try:
            asset = _get(f"{DVIDS_API}/asset", {"api_key": key, "id": asset_id})
        except Exception as e:
            print(f"⚠️ DVIDS asset {asset_id} failed ({type(e).__name__}).")
            continue
        if raw is not None and len(raw) < 4:
            raw.append({"asset": asset})
        a = asset.get("results") or asset
        title = (a.get("title") or hit.get("title") or "").strip()
        date = a.get("date") or a.get("date_published") or hit.get("date_published")
        credit = a.get("credit") if isinstance(a.get("credit"), list) else []
        artist = ", ".join(c.get("name", "") for c in credit if isinstance(c, dict))
        cand = {"title": title, "year": _year(date), "artist": artist or "DVIDS",
                "license": "Public domain (DVIDS)", "source": "DVIDS",
                "page": a.get("url") or hit.get("url")}
        if kind == "video":
            files = [f for f in (a.get("files") or [])
                     if isinstance(f, dict) and "mp4" in str(f.get("type", "")) + str(f.get("src", ""))]
            if not files:
                continue
            best = max(files, key=lambda f: int(f.get("width") or 0))
            dur = float(a.get("duration") or 0)
            if dur > 10000:                  # milliseconds
                dur /= 1000
            cand.update(url=best.get("src"), width=best.get("width"),
                        height=best.get("height"), duration=dur)
        else:
            img = a.get("image")
            if not img and isinstance(a.get("thumbnail"), dict):
                img = a["thumbnail"].get("url")
            dims = a.get("dimensions") or {}
            cand.update(url=img, width=dims.get("width"), height=dims.get("height"))
        if cand.get("url"):
            out.append(cand)
    return out


def search_openverse(query: str, limit: int = 12, raw=None) -> list:
    """Openverse photos for a query, commercial use and modification allowed."""
    try:
        found = _get(OPENVERSE_API, {"q": query, "page_size": limit,
                                     "license_type": "commercial,modification",
                                     "category": "photograph", "mature": "false"})
    except Exception as e:
        print(f"⚠️ Openverse search failed ({type(e).__name__}: {e}).")
        return []
    if raw is not None:
        raw.append({"openverse": (found.get("results") or [])[:2]})
    out = []
    for r in (found.get("results") or [])[:limit]:
        if not r.get("url"):
            continue
        lic = f"CC {str(r.get('license', '')).upper()} {r.get('license_version', '')}".strip()
        if str(r.get("license", "")).lower() in ("cc0", "pdm"):
            lic = "Public domain" if r.get("license") == "pdm" else "CC0"
        out.append({"url": r["url"], "title": (r.get("title") or "").strip(),
                    "year": _year(r.get("title")), "width": r.get("width"),
                    "height": r.get("height"), "artist": (r.get("creator") or "")[:60],
                    "license": lic, "source": f"Openverse / {r.get('source', '')}",
                    "page": r.get("foreign_landing_url")})
    return out
