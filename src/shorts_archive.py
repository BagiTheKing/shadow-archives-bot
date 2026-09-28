"""
Real archive pictures in the Shorts, beside the generated paintings.

Every frame of a Short used to be an AI oil painting. On a channel whose whole
claim is "this really happened", one real photograph of the ship, the man or
the battlefield is the proof a painting cannot be, and the long-form lanes
have had the search for it (archive_engine.py) all along.

Up to SHORTS_ARCHIVE_FRAMES pictures a video, from two places, in this order:
  1. the writer's archive_query for a scene, searched on Wikimedia Commons
     and the Library of Congress. A result is used only if its file title
     carries the query's words (archive_engine.footage_matches): an
     unrelated real photo reads worse than a good painting;
  2. the lead image of the story's Wikipedia article (shorts_facts), for the
     earliest scene still without one.

A picture does not replace the scene's painting: it becomes one of the scene's
shots (video_engine.plan_shots), so the generator's daily allowance is spent
exactly as before. Every picture is credited in the description.
"""

import os

from archive_engine import (article_lead_image, download_archival_image,
                            fit_vertical, footage_matches, format_credit,
                            search_archives)

ARCHIVE_FRAMES = int(os.getenv("SHORTS_ARCHIVE_FRAMES", "2"))
W, H = 1080, 1920
CANDIDATES_PER_QUERY = 6


def _fetch(candidate, scene, idx, build_dir):
    path = os.path.join(build_dir, f"archive_{idx}.jpg")
    if not download_archival_image(candidate, path, W, H, fit=fit_vertical):
        return False
    scene["archive_path"] = path
    scene["archive_credit"] = format_credit(candidate)
    print(f"🗄️  Scene {idx}: archive frame — {scene['archive_credit'][:90]}")
    return True


def add_archive_frames(script_data: dict, build_dir: str = "build") -> int:
    """Attaches archive_path/archive_credit to up to ARCHIVE_FRAMES scenes.
    Returns how many were found. Never raises: a Short without a real
    picture is still a Short."""
    if ARCHIVE_FRAMES <= 0:
        return 0
    scenes = [(i, s) for i, s in enumerate(script_data.get("scenes") or [])
              if not s.get("is_outro")]
    used, found = set(), 0
    try:
        for idx, scene in scenes:
            if found >= ARCHIVE_FRAMES:
                break
            query = (scene.get("archive_query") or "").strip()
            if not query:
                continue
            for cand in search_archives(query, limit=10)[:CANDIDATES_PER_QUERY]:
                if cand["url"] in used:
                    continue
                if not footage_matches(query, (cand.get("title") or "").lower()):
                    continue
                if _fetch(cand, scene, idx, build_dir):
                    used.add(cand["url"])
                    found += 1
                    break
            else:
                print(f"   🗄️  Scene {idx}: nothing on the archives matches "
                      f"{query!r}.")

        source_title = script_data.get("source_title")
        if found < ARCHIVE_FRAMES and source_title:
            lead = article_lead_image(source_title)
            # The hook is the painting's moment; the lead image goes to the
            # first scene after it that has no real picture yet.
            spare = [(i, s) for i, s in scenes[1:] if not s.get("archive_path")]
            if lead and lead["url"] not in used and spare:
                idx, scene = spare[0]
                if _fetch(lead, scene, idx, build_dir):
                    found += 1
    except Exception as e:
        print(f"⚠️ Archive frames skipped ({type(e).__name__}: {e}).")
    print(f"🗄️  {found} archive frame(s) in this Short.")
    return found


def archive_credits(script_data: dict) -> list:
    return [s["archive_credit"] for s in script_data.get("scenes") or []
            if s.get("archive_path") and s.get("archive_credit")]
