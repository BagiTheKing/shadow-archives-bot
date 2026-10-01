"""
Fixes one uploaded video's metadata in place, through the channel's own
YouTube credentials (run by .github/workflows/fix_video.yml):

- a new title, when given;
- placeholder lines in the description ("...", left by a model that echoed
  its template) replaced by the given text, every other line kept (credits,
  music attribution, chapters, sources);
- the privacy status, when given.

    VIDEO_ID=... TITLE=... INTRO=... PRIVACY=public python tools/fix_video.py
"""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from youtube_publisher import get_authenticated_service


def _placeholder(line):
    return line.strip() != "" and len(re.findall(r"[A-Za-z0-9]", line.replace("...", ""))) < 3


def main():
    vid = os.environ["VIDEO_ID"].strip()
    title = os.getenv("TITLE", "").strip()
    intro = os.getenv("INTRO", "").strip()
    privacy = os.getenv("PRIVACY", "").strip()
    yt = get_authenticated_service()
    items = yt.videos().list(part="snippet,status", id=vid).execute().get("items", [])
    if not items:
        sys.exit(f"Video {vid} not found on this channel.")
    snip, status = items[0]["snippet"], items[0]["status"]
    print(f"Before: {snip['title']!r} [{status['privacyStatus']}]")
    lines = (snip.get("description") or "").split("\n")
    out, replaced = [], False
    for line in lines:
        if _placeholder(line):
            if intro and not replaced:
                out.append(intro)
                replaced = True
            continue
        out.append(line)
    if intro and not replaced:
        out.insert(0, intro)
    desc = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()
    body = {"id": vid, "snippet": {
        "title": title or snip["title"], "description": desc[:4990],
        "categoryId": snip.get("categoryId", "27"), "tags": snip.get("tags", []),
        **({"defaultLanguage": snip["defaultLanguage"]} if snip.get("defaultLanguage") else {}),
    }}
    parts = "snippet"
    if privacy:
        body["status"] = {"privacyStatus": privacy,
                          "selfDeclaredMadeForKids": status.get("selfDeclaredMadeForKids", False)}
        parts = "snippet,status"
    thumb = os.getenv("THUMB", "").strip()
    if thumb:
        # set before anything goes public: the old thumbnail may carry the
        # same placeholder text
        from youtube_publisher import set_thumbnail
        set_thumbnail(yt, vid, thumb)
    yt.videos().update(part=parts, body=body).execute()
    print(f"After:  {body['snippet']['title']!r} [{privacy or status['privacyStatus']}]")
    print("Description starts: " + desc[:200].replace("\n", " | "))


if __name__ == "__main__":
    main()
