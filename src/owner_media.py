"""
Pictures the owner made or chose for one video (owner, 26 Oct: "give me the
prompts for the images and I will generate them"). OWNER_MEDIA_DIR names a
folder in the repository, e.g. owner_media/israel-nuclear/, holding any of:

- now.jpg / now.png / now.mp4    today's side of the THEN / NOW opening
- then.jpg / then.png / then.mp4 the past's side
- scene_012.jpg (or .png)        scene 12's picture, in place of a generated
                                 one; scene_012_1.jpg is its second shot
- sources.json                   {"scene_000": {"url": ..., "credit": ...}}:
                                 a free picture by its link instead of a file

Scene numbers are the ones a run prints ("Scene 12: generating shot 1/1")
and writes to build/image_prompts.json, so a re-run of the same saved
script lines up. A missing folder or file changes nothing.
"""

import glob
import json
import os

IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp")
CREDIT = "Illustration made for this video"


def _dir():
    d = os.getenv("OWNER_MEDIA_DIR", "").strip()
    return d if d and os.path.isdir(d) else None


def _from_sources(d, stem):
    """sources.json in the folder: {"scene_000": {"url": ..., "credit": ...}}
    for a free picture chosen by link (a Commons file the review session
    could not download), fetched once into the folder."""
    try:
        with open(os.path.join(d, "sources.json"), encoding="utf-8") as f:
            entry = json.load(f).get(stem)
    except (FileNotFoundError, ValueError):
        return None
    if not entry or not entry.get("url"):
        return None
    path = os.path.join(d, stem + ".jpg")
    if not os.path.exists(path):
        from archive_engine import download_archival_image
        if not download_archival_image({"url": entry["url"], "title": stem}, path, 2880, 1620):
            return None
    _CREDITS[path] = entry.get("credit") or CREDIT
    return path


_CREDITS = {}


def credit_of(path):
    return _CREDITS.get(path, CREDIT)


def _first(stem, exts):
    d = _dir()
    if not d:
        return None
    for ext in exts:
        for p in glob.glob(os.path.join(d, stem + ext)) + glob.glob(os.path.join(d, stem + ext.upper())):
            return p
    return _from_sources(d, stem)


def opening(side):
    """(path, credit) for "now" or "then", or None."""
    p = _first(side, (".mp4",) + IMAGE_EXT)
    if p:
        print(f"🎨 Owner's {side.upper()} for the opening: {os.path.basename(p)}")
        return p, credit_of(p)
    return None


def scene_images(idx):
    """The owner's pictures for scene idx, shot order, or []."""
    out = []
    first = _first(f"scene_{idx:03d}", IMAGE_EXT)
    if first:
        out.append(first)
    for shot in range(1, 4):
        p = _first(f"scene_{idx:03d}_{shot}", IMAGE_EXT)
        if p:
            out.append(p)
    return out


def opening_settings():
    """opening.json in the folder: {"then_year": 1960, "then_date": null,
    "lines": ["today's line", "the past's line"]}, or {}."""
    d = _dir()
    if not d:
        return {}
    try:
        with open(os.path.join(d, "opening.json"), encoding="utf-8") as f:
            return json.load(f) or {}
    except (FileNotFoundError, ValueError):
        return {}


def excluded(title):
    """Whether a picture's title matches the folder's exclude.json, a list
    of words that keep a picture out of this video (["Little Boy"])."""
    d = _dir()
    if not d or not title:
        return False
    try:
        with open(os.path.join(d, "exclude.json"), encoding="utf-8") as f:
            words = json.load(f) or []
    except (FileNotFoundError, ValueError):
        return False
    return any(w.lower() in title.lower() for w in words)
