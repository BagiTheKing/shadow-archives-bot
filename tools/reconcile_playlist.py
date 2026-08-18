"""
File every published video into the series playlist.

Two jobs, one piece of code, which is why this is a reconciler and not a
one-off backfill script.

THE BACKFILL. Fifty videos were published before the pipeline knew how to file
anything, and 9,152 views produced exactly one playlist view because there was
no playlist to view. The first run of this puts the whole back catalogue in
order.

THE DRIFT. youtube_publisher.add_to_playlist swallows its own failures on
purpose — a playlist hiccup must never sink a video that is already public — so
a run can half-succeed and nothing anywhere notices. That is a good trade at
upload time and a bad one forever after. This closes it: anything that missed
its ride gets filed on the next pass.

Which is the argument for keeping it rather than deleting it after the
catalogue is caught up. Asking the question costs about four quota units when
the answer is "nothing is missing", and a throwaway script would only have to
be written again the first time a run fails halfway.

    python tools/reconcile_playlist.py            # file everything missing
    python tools/reconcile_playlist.py --dry-run  # say what it would do

Needs the same credentials as publishing: YT_CLIENT_SECRET_JSON and
YT_REFRESH_TOKEN, minted with the force-ssl scope.

QUOTA. Listing is nearly free (1 unit a page). Each insert costs 50, so a full
catalogue of fifty is ~2,500 units of the 10,000-a-day allowance. MAX_ADDS caps
one run so this can share a day with a publish (~2,150 units) and the reach
checks (~100 a candidate) without anyone running out. Whatever it does not
reach today, it reaches tomorrow.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "src"))

from main import PLAYLIST_DESCRIPTION, SERIES_NAME          # noqa: E402
from youtube_publisher import (add_to_playlist, ensure_playlist,   # noqa: E402
                               get_authenticated_service)

# 30 inserts is 1,500 units, which leaves room for a same-day publish. Raise it
# with MAX_PLAYLIST_ADDS on a day with no scheduled run to finish sooner.
MAX_ADDS = int(os.getenv("MAX_PLAYLIST_ADDS", "30"))


def uploaded_video_ids(youtube):
    """Every video on the channel, oldest first.

    Read from the channel's own uploads playlist rather than search.list, which
    costs 100 units a page and can lag hours behind a new upload.
    """
    channels = youtube.channels().list(part="contentDetails", mine=True).execute()
    items = channels.get("items") or []
    if not items:
        raise SystemExit("❌ The credentials do not resolve to a channel.")
    uploads = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]

    ids, request = [], youtube.playlistItems().list(
        part="contentDetails,status", playlistId=uploads, maxResults=50)
    while request is not None:
        response = request.execute()
        for item in response.get("items", []):
            # A private or deleted video cannot be added to a playlist, and
            # trying costs a unit and an error apiece.
            if item.get("status", {}).get("privacyStatus") == "private":
                continue
            ids.append(item["contentDetails"]["videoId"])
        request = youtube.playlistItems().list_next(request, response)

    # The uploads playlist is newest-first; the series should read in the order
    # it was published, and new uploads append to the end from here on.
    return list(reversed(ids))


def playlist_video_ids(youtube, playlist_id):
    ids, request = set(), youtube.playlistItems().list(
        part="contentDetails", playlistId=playlist_id, maxResults=50)
    while request is not None:
        response = request.execute()
        for item in response.get("items", []):
            ids.add(item["contentDetails"]["videoId"])
        request = youtube.playlistItems().list_next(request, response)
    return ids


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="report what is missing without filing anything")
    parser.add_argument("--playlist", default=SERIES_NAME,
                        help=f"playlist title (default: {SERIES_NAME!r})")
    args = parser.parse_args()

    if not args.playlist:
        raise SystemExit("❌ No playlist title — set SERIES_NAME or pass "
                         "--playlist.")

    youtube = get_authenticated_service()

    playlist_id = ensure_playlist(youtube, args.playlist, PLAYLIST_DESCRIPTION)
    if not playlist_id:
        raise SystemExit("❌ Could not find or create the playlist. If the token "
                         "predates the force-ssl scope, mint a new one with "
                         "tools/get_youtube_refresh_token.py.")

    uploads = uploaded_video_ids(youtube)
    filed = playlist_video_ids(youtube, playlist_id)
    missing = [v for v in uploads if v not in filed]

    print(f"📼 {len(uploads)} public video(s) on the channel.")
    print(f"📂 {len(filed)} already in {args.playlist!r}.")

    if not missing:
        print("✅ Nothing to do — every video is filed.")
        return

    print(f"➕ {len(missing)} missing.")
    if args.dry_run:
        for video_id in missing:
            print(f"   would add https://youtu.be/{video_id}")
        return

    batch = missing[:MAX_ADDS]
    if len(missing) > len(batch):
        print(f"   Filing {len(batch)} this run to stay inside the daily quota; "
              f"{len(missing) - len(batch)} will follow on the next pass.")

    added = sum(1 for video_id in batch
                if add_to_playlist(youtube, playlist_id, video_id))
    print(f"✅ Filed {added}/{len(batch)}.")
    if added < len(batch):
        print("   The rest will be retried next run — nothing here is "
              "destructive, and adding a video twice is not possible.")


if __name__ == "__main__":
    main()
