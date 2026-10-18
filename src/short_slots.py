"""
At most SHORTS_PER_DAY Shorts go public on any UTC day (owner, 2026-10-18).

On 18 Oct the channel put out six Shorts in about twenty hours (two Ukraine
clips, three "40 Acres" clips and the Sunday daily Short) and the last three
sat at zero views: a small channel's new Shorts share one test audience, so
crowding them starves each other. Nothing is dropped. A Short that would be
the day's fourth is scheduled into the next free slot instead, at least
MIN_GAP_HOURS after any other Short.

The channel is the source of truth: Shorts already public, and private ones
scheduled with publishAt, are read back from the uploads playlist (2 quota
units). Any failure there returns "publish now", as before.
"""

import datetime as dt
import os

SHORTS_PER_DAY = int(os.getenv("SHORTS_PER_DAY", "3"))
MIN_GAP_HOURS = float(os.getenv("SHORT_GAP_HOURS", "6"))


def _parse(t):
    return dt.datetime.fromisoformat(t.replace("Z", "+00:00")) if t else None


def channel_short_times(youtube, limit=50):
    """Publish times (UTC) of the channel's recent and scheduled Shorts."""
    ch = youtube.channels().list(part="contentDetails", mine=True).execute()
    uploads = ch["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
    items = youtube.playlistItems().list(part="contentDetails", playlistId=uploads,
                                         maxResults=limit).execute().get("items", [])
    ids = [i["contentDetails"]["videoId"] for i in items]
    if not ids:
        return []
    vids = youtube.videos().list(part="snippet,status", id=",".join(ids)).execute()
    times = []
    for v in vids.get("items", []):
        if "#shorts" not in (v["snippet"].get("title") or "").lower():
            continue
        st = v["status"]
        if st.get("privacyStatus") == "public":
            t = _parse(v["snippet"].get("publishedAt"))
        elif st.get("publishAt"):
            t = _parse(st["publishAt"])
        else:
            continue
        if t:
            times.append(t)
    return times


def next_slot(taken, earliest, per_day=SHORTS_PER_DAY, gap_hours=MIN_GAP_HOURS):
    """The first time at or after `earliest` whose UTC day has fewer than
    per_day Shorts and that sits gap_hours away from every taken time."""
    gap = dt.timedelta(hours=gap_hours)
    t = earliest
    for _ in range(24 * 14):
        same_day = [x for x in taken if x.date() == t.date()]
        clash = [x for x in taken if abs(x - t) < gap]
        if len(same_day) < per_day and not clash:
            return t
        if len(same_day) >= per_day:
            # first moment of the next day (a gap check still applies there)
            t = dt.datetime.combine(t.date() + dt.timedelta(days=1), dt.time(0, 0),
                                    tzinfo=dt.timezone.utc) + dt.timedelta(minutes=13)
        else:
            t = max(clash) + gap
    return t


def plan(youtube, count, first_now=True):
    """Publish times for `count` new Shorts, in order: None means public now.
    The first one goes out now when the day still has room and it clashes
    with nothing; every later one follows the cap and the gap."""
    now = dt.datetime.now(dt.timezone.utc)
    try:
        taken = channel_short_times(youtube)
    except Exception as e:
        print(f"⚠️ Shorts schedule unreadable ({type(e).__name__}: {e}) — "
              "using the plain 6-hour spacing.")
        taken = []
    out = []
    t = now
    for k in range(count):
        slot = next_slot(taken, t)
        if k == 0 and first_now and slot == now:
            out.append(None)
        else:
            # a scheduled time must be some minutes in the future for YouTube
            slot = max(slot, now + dt.timedelta(minutes=15))
            out.append(slot)
        taken.append(slot)
        t = slot + dt.timedelta(hours=MIN_GAP_HOURS)
    return out


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ") if t else None
