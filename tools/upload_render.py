"""
Uploads a finished (spliced) render with the owner's chosen title,
description, tags, thumbnail and first comment from <owner folder>/upload.json,
instead of letting the pipeline render it again. Run by
.github/workflows/upload_render.yml.

    python tools/upload_render.py video.mp4 owner_media/<story>
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from youtube_publisher import upload_video_to_youtube  # noqa: E402

SERIES_NAME = os.getenv("SERIES_NAME", "Catastrophe Files")


def main(video, folder):
    with open(os.path.join(folder, "upload.json"), encoding="utf-8") as f:
        up = json.load(f)
    thumb = os.path.join(folder, up["thumbnail"]) if up.get("thumbnail") else None
    if len(up["title"]) > 100 or len(up["description"]) > 5000:
        raise SystemExit("❌ Title over 100 or description over 5000 characters.")
    video_id = upload_video_to_youtube(video, up["title"], up["description"], up.get("tags"),
                                       thumbnail_path=thumb,
                                       first_comment=up.get("first_comment"),
                                       playlist_title=SERIES_NAME)
    if not video_id:
        raise SystemExit("❌ Upload failed.")
    os.makedirs("build", exist_ok=True)
    with open(os.path.join("build", "uploaded.json"), "w", encoding="utf-8") as f:
        json.dump({"video_id": video_id}, f)


if __name__ == "__main__":
    main(*sys.argv[1:3])
