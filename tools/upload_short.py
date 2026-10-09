"""
Uploads a Short rendered by a dry run (daily_publish.yml with skip_upload),
as it is, without rendering it again (run by upload_short.yml).

The dry run keeps the video and script.json as artifacts; the archive
credits and the music credit are rebuilt here the way the run made them
(the archive search is repeated for the same queries; MUSIC names the
track the run's log shows), so the description carries every required
credit.

    VIDEO=out/final_short.mp4 SCRIPT=out/script.json EPISODE=32 \
    MUSIC="Epilog - Ghostpocalypse - Kevin MacLeod.mp3" python tools/upload_short.py
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from main import (PLAYLIST_DESCRIPTION, SERIES_NAME, build_first_comment,
                  build_metadata)
from music_attribution import attribution_for
from shorts_archive import add_archive_frames
from youtube_publisher import upload_video_to_youtube


def main():
    with open(os.environ["SCRIPT"], encoding="utf-8") as f:
        script = json.load(f)
    add_archive_frames(script, tempfile.mkdtemp())
    music = os.getenv("MUSIC", "").strip()
    if music:
        script["music_attribution"] = attribution_for(music)
    episode = int(os.getenv("EPISODE", "0")) or None
    title, description, tags = build_metadata(script, episode)
    print(f"📋 Title: {title}\n--- description ---\n{description}\n---")
    if os.getenv("DRY", "0") == "1":
        return
    vid = upload_video_to_youtube(os.environ["VIDEO"], title, description, tags,
                                  first_comment=build_first_comment(script),
                                  playlist_title=SERIES_NAME,
                                  playlist_description=PLAYLIST_DESCRIPTION)
    if not vid:
        sys.exit("Upload failed.")


if __name__ == "__main__":
    main()
