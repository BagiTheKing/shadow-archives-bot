"""
Thumbnail rotation: YouTube's Test & Compare done by the pipeline itself.

Test & Compare has no API, so a video's three thumbnails (thumb_variants.py)
are tried one after another instead: each is shown for ROTATE_DAYS once the
video is public, the views it collected per day are recorded, and when all
have had their turn the best one is set for good. A private video waits: its
clock only starts when the owner makes it public.

State lives on the `thumb-rotation` branch (checked out at ROT_DIR by the
workflow): state.json plus videos/<video_id>/<n>_<kind>.jpg.

    python tools/thumb_rotation.py register <video_id> <thumbnails dir>
    python tools/thumb_rotation.py rotate

Weakness, stated plainly: the periods are sequential, and a video gets most
of its views early, so the first thumbnail has a head start. Views per day
are compared, which softens it, and at a few dozen views per video the
difference is mostly chance. It is still better than never trying the others.
"""

import datetime as dt
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

ROT_DIR = os.environ.get("ROT_DIR", "rot")
ROTATE_DAYS = float(os.environ.get("ROTATE_DAYS", "3"))
STATE = os.path.join(ROT_DIR, "state.json")


def _now():
    return dt.datetime.now(dt.timezone.utc)


def _load():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"videos": {}}


def _save(state):
    os.makedirs(ROT_DIR, exist_ok=True)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def register(video_id, thumbs_dir):
    files = sorted(f for f in os.listdir(thumbs_dir) if f.lower().endswith(".jpg"))
    if len(files) < 2:
        print(f"Only {len(files)} thumbnail(s) for {video_id} — nothing to rotate.")
        return
    dest = os.path.join(ROT_DIR, "videos", video_id)
    os.makedirs(dest, exist_ok=True)
    for f in files:
        shutil.copy(os.path.join(thumbs_dir, f), os.path.join(dest, f))
    state = _load()
    state["videos"][video_id] = {
        "files": [f"videos/{video_id}/{f}" for f in files],
        "status": "waiting",          # waiting -> testing -> done
        "current": None, "since": None, "views_at_start": None,
        "results": [], "registered": _now().isoformat(),
    }
    _save(state)
    print(f"Registered {video_id} for rotation: {', '.join(files)}")


def _set(youtube, video_id, path):
    from youtube_publisher import set_thumbnail
    return set_thumbnail(youtube, video_id, os.path.join(ROT_DIR, path))


def rotate():
    from youtube_publisher import get_authenticated_service
    state = _load()
    live = {k: v for k, v in state["videos"].items() if v["status"] != "done"}
    if not live:
        print("No video is being rotated.")
        return
    youtube = get_authenticated_service()
    got = youtube.videos().list(part="status,statistics", id=",".join(live)).execute()
    info = {i["id"]: i for i in got.get("items", [])}
    now = _now()
    for vid, v in live.items():
        item = info.get(vid)
        if not item:
            print(f"{vid}: not found (deleted?) — dropped from the rotation.")
            v["status"] = "done"
            continue
        views = int(item.get("statistics", {}).get("viewCount", 0))
        if item.get("status", {}).get("privacyStatus") != "public":
            print(f"{vid}: not public yet — waiting.")
            continue
        if v["status"] == "waiting":
            if _set(youtube, vid, v["files"][0]):
                v.update(status="testing", current=0, since=now.isoformat(),
                         views_at_start=views)
                print(f"{vid}: public — rotation started with {v['files'][0]}.")
            continue
        days = (now - dt.datetime.fromisoformat(v["since"])).total_seconds() / 86400
        if days < ROTATE_DAYS:
            print(f"{vid}: {v['files'][v['current']]} for {days:.1f} of "
                  f"{ROTATE_DAYS:g} days ({views - v['views_at_start']} views so far).")
            continue
        gained = views - v["views_at_start"]
        result = {"file": v["files"][v["current"]], "views": gained,
                  "days": round(days, 2), "per_day": round(gained / max(days, 0.01), 2)}
        print(f"{vid}: {result['file']} — {gained} views in {days:.1f} days.")
        nxt = v["current"] + 1
        # a result is only recorded once the switch it closes succeeded, so a
        # failed swap retried tomorrow does not count the same period twice
        if nxt < len(v["files"]):
            if _set(youtube, vid, v["files"][nxt]):
                v["results"].append(result)
                v.update(current=nxt, since=now.isoformat(), views_at_start=views)
                print(f"{vid}: switched to {v['files'][nxt]}.")
        else:
            best = max(v["results"] + [result], key=lambda r: r["per_day"])
            if _set(youtube, vid, best["file"]):
                v["results"].append(result)
                v.update(status="done", winner=best["file"], current=None)
                print(f"{vid}: done — kept {best['file']} "
                      f"({best['per_day']} views/day). All: "
                      + "; ".join(f"{r['file']}: {r['per_day']}/day" for r in v["results"]))
    _save(state)


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "register":
        register(sys.argv[2], sys.argv[3])
    elif len(sys.argv) >= 2 and sys.argv[1] == "rotate":
        rotate()
    else:
        print(__doc__)
        sys.exit(2)
