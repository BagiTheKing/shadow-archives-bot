"""
Uploads one finished Short from the preview-media branch (a native Short
the owner approved in preview) in the next free Shorts slot at or after a
chosen time, with its first comment held until it is public. Run by
.github/workflows/upload_short.yml.

    python tools/upload_short.py video.mp4 meta.json
"""

import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def main(video, meta_path):
    import short_slots
    from youtube_publisher import get_authenticated_service, upload_video_to_youtube
    meta = json.load(open(meta_path, encoding="utf-8"))
    now = dt.datetime.now(dt.timezone.utc)
    earliest = now
    if meta.get("not_before"):
        earliest = max(now, dt.datetime.fromisoformat(meta["not_before"].replace("Z", "+00:00")))
    taken = short_slots.channel_short_times(get_authenticated_service())
    slot = short_slots.next_slot(taken, earliest)
    publish_at = None if (slot - now).total_seconds() < 600 else short_slots.iso(slot)
    print(f"🗓️  {'Public now' if not publish_at else 'Scheduled for ' + publish_at}")
    video_id = upload_video_to_youtube(video, meta["title"], meta["description"],
                                       ["history", "shorts", "documentary", "yemen"],
                                       first_comment=meta.get("comment"),
                                       publish_at=publish_at)
    if not video_id:
        sys.exit("❌ Upload failed.")
    print(f"✅ https://youtube.com/shorts/{video_id}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
