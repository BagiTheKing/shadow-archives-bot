"""
Stories researched ahead, one queue per lane.

The daily scout (tools/topic_scout.py) finds each lane's next topics and
builds their story (Wikipedia research, verified fact sheet, outline) with
the day's leftover quota. A video run then starts from a story that is
already checked, instead of choosing and researching a topic inside the run,
where one bad topic (an idea, an unreadable article, a refused request) used
to cost the whole video.

Files: STORY_QUEUE_DIR/<lane>/<timestamp>.json, the workflows keeping that
folder on the `topic-queue` branch. Without STORY_QUEUE_DIR nothing is
queued and runs research for themselves, as before.
"""

import datetime as dt
import glob
import json
import os

# A trend story is about this week's news: it goes stale fast. An encore
# story (a best-liked Short, the general list) keeps.
# Two days: the scout refills it twice a day, and a trend picked on the 6th
# was still waiting on the 8th, the anniversary of 7 October.
MAX_AGE_DAYS = {"timely": 2, "encore": 21}


def _dir(lane):
    d = os.getenv("STORY_QUEUE_DIR", "").strip()
    return os.path.join(d, lane) if d else None


def _now():
    return dt.datetime.now(dt.timezone.utc)


def entries(lane):
    """[(path, entry)] still fresh, oldest first; stale ones are deleted."""
    d = _dir(lane)
    if not d or not os.path.isdir(d):
        return []
    out = []
    for path in sorted(glob.glob(os.path.join(d, "*.json"))):
        try:
            with open(path, encoding="utf-8") as f:
                e = json.load(f)
            age = _now() - dt.datetime.fromisoformat(e["created"])
            # the trend lane holds trend stories only: a general-list
            # fallback (the Great Fire of London, 8 Oct) would take the
            # slot of whatever the news is about by the time it runs
            off_lane = lane == "timely" and e.get("source") != "timely"
            if age.days >= MAX_AGE_DAYS.get(lane, 7) or not e.get("story") or off_lane:
                why = "not from the news" if off_lane else "too old"
                print(f"🗑️  Queued story {why}, dropped: {e.get('topic', '')[:70]}")
                os.remove(path)
                continue
            out.append((path, e))
        except Exception as ex:
            print(f"🗑️  Unreadable queued story dropped ({type(ex).__name__}): {path}")
            os.remove(path)
    return out


def topics(lane):
    return [e["topic"] for _, e in entries(lane)]


def take(lane):
    """The oldest fresh story for this lane, removed from the queue, or None."""
    got = entries(lane)
    if not got:
        return None
    path, e = got[0]
    os.remove(path)
    return e


def put(lane, entry):
    d = _dir(lane)
    if not d:
        return None
    if lane == "timely" and entry.get("source") != "timely":
        print(f"↩️  Not queued for timely — not from the news: {entry['topic'][:70]}")
        return None
    os.makedirs(d, exist_ok=True)
    entry = {**entry, "created": _now().isoformat(), "lane": lane}
    path = os.path.join(d, _now().strftime("%Y%m%dT%H%M%S") + ".json")
    with open(path + ".part", "w", encoding="utf-8") as f:
        json.dump(entry, f, ensure_ascii=False)
    os.replace(path + ".part", path)
    print(f"📦 Queued for {lane}: {entry['topic'][:90]}")
    return path
