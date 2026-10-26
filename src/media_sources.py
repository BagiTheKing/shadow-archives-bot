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


# DVIDS search is full text over captions: "Aden" also finds "Hickam", and
# "Houthi" finds a medal ceremony whose caption mentions the deployment.
# What it returns must name the subject in its title or caption, and never
# be a ceremony, a visit or a promotional reel (probe, 20 Oct).
_JUNK = re.compile(r"\b(ceremony|change of command|decoration|award|visit|wwe|"
                   r"dental|clinic|retire|promotion|birthday|hype|music|band|"
                   r"graduat|recruit|reenlist|5k|memorial run|feature|"
                   r"interview|podcast|hometown|lecture|talk|panel|seminar|"
                   r"symposium|presentation|discussion|speaker|book|history of|"
                   r"remembrance|anniversary|commemorat|museum|exhibit|threat is real|"
                   r"explained|explainer|animation|infographic|psa|trailer|"
                   r"hype|promo|recap|highlights)", re.I)
# Pictures that are never about this channel's stories, whatever their
# title shares with the search: an "England national football team, 1959"
# team photo and a cartoon cat stood in a Cuban Missile Crisis video (25 Oct).
OFF_TOPIC = re.compile(r"\b(football|soccer|national team|basketball|baseball|"
                       r"rugby|cricket|olympic|athletics|cartoon|animated|"
                       r"animation|comic strip|felix the cat|disney|mickey)\b", re.I)
_STOP = {"the", "and", "with", "from", "into", "over", "against", "attack",
         "attacks", "launch", "launches", "war", "civil", "missile", "strike",
         "strikes", "forces"}


def _is_name(query) -> bool:
    words = [w for w in re.sub(r"\b\d{4}\b", "", query).split() if w]
    return len(words) >= 2 and all(w[:1].isupper() or w.lower().startswith(("al-", "el-"))
                                   for w in words)


def relevant(cand, query) -> bool:
    """The TITLE must name the subject; a caption mentioning it is not
    enough (an untitled 2001 Defense Department portrait came back for "Ali
    Abdullah Saleh" and stood in for him, 21 Oct). For a person, every part
    of the name must be in the title."""
    title = (cand.get("title") or "").lower()
    if _JUNK.search(title):
        return False
    words = [w for w in re.findall(r"[a-z]{4,}", query.lower()) if w not in _STOP]
    if not words:
        return True
    hits = [bool(re.search(rf"\b{re.escape(w[:5])}", title)) for w in words]
    return all(hits) if _is_name(query) else any(hits)


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
        cand = {"title": title, "text": (a.get("description") or "")[:600],
                "year": _year(date), "artist": artist or "DVIDS",
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
        if cand.get("url") and relevant(cand, query):
            out.append(cand)
    return out


# Openverse timed out on every search from GitHub's runners (probe, 20 Oct):
# the first failure retires it for the run instead of costing every scene a
# minute.
_openverse_down = False


def search_openverse(query: str, limit: int = 12, raw=None) -> list:
    """Openverse photos for a query, commercial use and modification allowed."""
    global _openverse_down
    if _openverse_down:
        return []
    try:
        found = _get(OPENVERSE_API, {"q": query, "page_size": limit,
                                     "license_type": "commercial,modification",
                                     "category": "photograph", "mature": "false"},
                     timeout=60)
    except Exception as e:
        print(f"⚠️ Openverse search failed ({type(e).__name__}: {e}) — not "
              "asked again this run.")
        _openverse_down = True
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
        cand = {"url": r["url"], "title": (r.get("title") or "").strip(),
                "year": _year(r.get("title")), "width": r.get("width"),
                "height": r.get("height"), "artist": (r.get("creator") or "")[:60],
                "license": lic, "source": f"Openverse / {r.get('source', '')}",
                "page": r.get("foreign_landing_url"),
                "text": " ".join(t.get("name", "") for t in r.get("tags") or []
                                 if isinstance(t, dict))}
        if relevant(cand, query):
            out.append(cand)
    return out
