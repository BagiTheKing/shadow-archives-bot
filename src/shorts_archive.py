"""
Real archive pictures in the Shorts, in place of generated paintings.

Every frame of a Short used to be an AI oil painting. On a channel whose whole
claim is "this really happened", a real photograph of the ship, the man or
the battlefield is the proof a painting cannot be, and the long-form lanes
already live on it (run #48: 69 of its 87 frames were archive).

Each shot (shorts_shots.py) that has an archive_query is searched on Wikimedia
Commons and the Library of Congress, before any painting is generated. A
result is used only if its file title carries the query's words
(archive_engine.footage_matches): an unrelated real photo reads worse than a
good painting. When one is found it IS that shot's picture, and the generator
skips the shot, which is also what keeps a Short inside the day's generator
allowance. The story's Wikipedia lead image fills the first shot of scene 2
when no search found anything. At most SHORTS_ARCHIVE_FRAMES a video, no
picture twice, every one credited in the description.
"""

import os

from archive_engine import (article_lead_image, download_archival_image,
                            fit_vertical, footage_matches, format_credit,
                            search_archives)

ARCHIVE_FRAMES = int(os.getenv("SHORTS_ARCHIVE_FRAMES", "5"))
W, H = 1080, 1920
CANDIDATES_PER_QUERY = 6


def _story_shots(script_data):
    for i, scene in enumerate(script_data.get("scenes") or []):
        if scene.get("is_outro"):
            continue
        for k, shot in enumerate(scene.get("shots") or []):
            yield i, k, shot


def _fetch(candidate, shot, label, build_dir):
    path = os.path.join(build_dir, f"archive_{label}.jpg")
    if not download_archival_image(candidate, path, W, H, fit=fit_vertical):
        return False
    shot["image_path"] = path
    shot["image_source"] = "archive"
    shot["credit"] = format_credit(candidate)
    print(f"🗄️  Shot {label}: archive picture — {shot['credit'][:90]}")
    return True


def add_archive_frames(script_data: dict, build_dir: str = "build") -> int:
    """Gives up to ARCHIVE_FRAMES shots a real picture. Returns how many.
    Never raises: a Short without a real picture is still a Short."""
    if ARCHIVE_FRAMES <= 0:
        return 0
    used, found = set(), 0
    try:
        for i, k, shot in _story_shots(script_data):
            if found >= ARCHIVE_FRAMES:
                break
            query = (shot.get("archive_query") or "").strip()
            if not query:
                continue
            for cand in search_archives(query, limit=10)[:CANDIDATES_PER_QUERY]:
                if cand["url"] in used:
                    continue
                if not footage_matches(query, (cand.get("title") or "").lower()):
                    continue
                if _fetch(cand, shot, f"{i}_{k}", build_dir):
                    used.add(cand["url"])
                    found += 1
                    break
            else:
                print(f"   🗄️  Shot {i}_{k}: nothing on the archives matches "
                      f"{query!r}.")

        source_title = script_data.get("source_title")
        if found == 0 and source_title:
            lead = article_lead_image(source_title)
            spare = [(i, k, s) for i, k, s in _story_shots(script_data)
                     if i >= 1 and k == 0 and not s.get("image_path")]
            if lead and spare:
                i, k, shot = spare[0]
                if _fetch(lead, shot, f"{i}_{k}", build_dir):
                    found += 1
    except Exception as e:
        print(f"⚠️ Archive pictures skipped ({type(e).__name__}: {e}).")
    print(f"🗄️  {found} archive picture(s) in this Short.")
    return found


def archive_credits(script_data: dict) -> list:
    credits = []
    for _, _, shot in _story_shots(script_data):
        if shot.get("image_source") == "archive" and shot.get("credit") \
                and shot["credit"] not in credits:
            credits.append(shot["credit"])
    return credits
