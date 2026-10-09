"""
The daily topic scout: keeps each lane's queue of researched stories full
(story_queue.py), so a video run never has to find and research its own
topic.

For each lane it asks the same finder the video runs use (find_story in
longform_main.py: watched channel or best-liked Short, ideas turned into
events, the general list as the last resort), builds the verified fact sheet
and outline, and queues the result. Topics already queued in any lane, or
being resumed by a lane's saved progress, are never picked twice.

    STORY_QUEUE_DIR=queue SCOUT_LANES=timely,encore python tools/topic_scout.py
"""

import glob
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import story_queue
from longform_llm import QuotaExhausted
from longform_main import find_story, load_published, describe

# One ready story per lane: a trend story goes stale in days, and the next
# scout refills the queue every day.
DEPTH = {"timely": 1, "encore": 1}


def _resuming():
    """Topics a lane's saved progress will resume (run_cache.py), read from
    the run-cache folders the workflow checks out at RUN_CACHE_ROOT."""
    root = os.getenv("RUN_CACHE_ROOT", "").strip()
    out = set()
    for p in glob.glob(os.path.join(root, "*", "state.json")) if root else []:
        try:
            with open(p, encoding="utf-8") as f:
                out.add(json.load(f)["topic"])
        except Exception:
            pass
    return out


def main():
    lanes = [x.strip() for x in os.getenv("SCOUT_LANES", "timely,encore").split(",") if x.strip()]
    history = load_published()
    print(f"🧾 History: {describe(history)}")
    for lane in lanes:
        os.environ["LONGFORM_LANE"] = lane
        want = DEPTH.get(lane, 1)
        while len(story_queue.entries(lane)) < want:
            taken = _resuming()
            for other in ("timely", "encore"):
                taken.update(story_queue.topics(other))
            print(f"🔭 Scouting a story for {lane} ({len(story_queue.entries(lane))}/{want} ready)...")
            try:
                found = find_story(lane, history, avoid=taken)
            except QuotaExhausted as e:
                print(f"🛑 Quota gone while scouting ({e}) — the next scout carries on.")
                return
            if not found:
                print(f"↩️  No story found for {lane} this time.")
                break
            if not story_queue.put(lane, found):
                # a trend slot is only filled from the news: the video run
                # tries the news again itself rather than take a fallback
                break
    for lane in lanes:
        print(f"📦 {lane}: {len(story_queue.entries(lane))} story(ies) ready — "
              + "; ".join(t[:60] for t in story_queue.topics(lane)))


if __name__ == "__main__":
    main()
