"""
Lists the channel's scheduled uploads (private with a publishAt) and, with
--hold, takes each off its schedule: it stays private and unpublished until
the owner releases it. Run by .github/workflows/hold_scheduled.yml.

    python tools/hold_scheduled.py [--hold]
"""

import datetime as dt
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def main(hold):
    from youtube_publisher import get_authenticated_service
    yt = get_authenticated_service()
    ch = yt.channels().list(part="contentDetails", mine=True).execute()
    uploads = ch["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
    items = yt.playlistItems().list(part="contentDetails", playlistId=uploads,
                                    maxResults=50).execute().get("items", [])
    ids = [i["contentDetails"]["videoId"] for i in items]
    vids = yt.videos().list(part="snippet,status", id=",".join(ids)).execute()["items"]
    now = dt.datetime.now(dt.timezone.utc)
    print(f"Latest {len(vids)} uploads:")
    for v in vids:
        st, sn = v["status"], v["snippet"]
        when = st.get("publishAt") or sn.get("publishedAt")
        print(f"  {v['id']}  {st.get('privacyStatus'):8}  {when}  {sn['title'][:70]}")
    scheduled = [v for v in vids if v["status"].get("privacyStatus") == "private"
                 and v["status"].get("publishAt")
                 and dt.datetime.fromisoformat(v["status"]["publishAt"].replace("Z", "+00:00")) > now]
    print(f"\n{len(scheduled)} scheduled to go public later.")
    for v in scheduled:
        if not hold:
            continue
        st = {"privacyStatus": "private", "selfDeclaredMadeForKids": False}
        yt.videos().update(part="status", body={"id": v["id"], "status": st}).execute()
        print(f"  ⏸️  held (private, unscheduled): {v['id']}  {v['snippet']['title'][:70]}")


if __name__ == "__main__":
    main("--hold" in sys.argv)
