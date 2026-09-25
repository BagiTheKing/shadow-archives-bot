"""
Channel watch — same-day candidate subjects from a named news/politics
channel, checked ahead of the usual trend feeds.

WHY THIS EXISTS
----------------
Google Trends and Google News (trend_source.py) sample search demand and
wire headlines. A live political talk show like Piers Morgan Uncensored
often has a heated, highly-searched debate up on YouTube hours before it
shows up in either of those — Trends lags real interest, and News RSS
favours wire copy over commentary shows. Checking a specific channel's
last-24h uploads catches that gap. This is opt-in and off by default (see
WATCH_CHANNEL_HANDLE); resolve_topic in longform_main.py tries it first
for the "timely" lane and falls through to trend_source, then the static
pool, exactly as it already did before this file existed.

THE SAFETY BOUNDARY — read before changing anything below
-----------------------------------------------------------
A political debate show routinely covers two different things in the same
segment: (1) a genuine geopolitical or religious/political THEME (Islam
and Western politics, the Israel-Palestine conflict, immigration policy),
and (2) a named living guest's personal conduct, health or character,
thrown around as ammunition in the argument (an addiction claim, an
affair, a messy divorce, an unverified allegation from a third party).
This channel can safely make a documentary about (1) — formats.py's THE
FAULT LINE exists for exactly that, roots only, never the live dispute.
It can never safely make one about (2): an AI pipeline that writes with
free models known to hallucinate, renders with AI imagery, and uploads
without a human reading the script first, repeating an unverified
personal claim about a named, living, identifiable person is a real
defamation and privacy exposure, not a style problem.

So this module NEVER hands a video's title or description forward AS A
CANDIDATE SUBJECT ITSELF. The theme extraction below (see
longform_llm.extract_channel_subjects, called from get_watch_candidates)
does read the raw title and description — an earlier version matched a
fixed, hand-curated keyword list instead specifically to avoid that, but
that approach had two real failures: no bucket at all for a real recurring
story (Yemen/the Houthis had none), and when a description matched more
than one bucket, whichever was listed first won regardless of which was
actually the segment's subject — "safest", not "most important". Reading
the text directly fixes both, at the cost of needing the same safety rule
enforced INSIDE that prompt instead of by construction: the model is told,
explicitly, to drop any upload that is really about a named living
person's personal conduct, health, relationships or an unverified
allegation, rather than label it even generically. That is the first of
two gates, not the only one — whatever gets through is still subject to
every one of select_historical_anchor's existing hard rules (settled
pre-cutoff event only, no taking sides, and never a topic centred on a
named living private individual), so nothing about a real person's health,
relationships or unverified conduct can reach a script even if the source
video was mostly about exactly that, and even if the first gate misses it.
"""

import datetime
import os

# Off by default: set to a channel's @handle to enable. Piers Morgan
# Uncensored was the channel that prompted this feature.
WATCH_CHANNEL_HANDLE = os.getenv("WATCH_CHANNEL_HANDLE", "")
WATCH_LOOKBACK_HOURS = int(os.getenv("WATCH_LOOKBACK_HOURS", "48"))
WATCH_MAX_VIDEOS = int(os.getenv("WATCH_MAX_VIDEOS", "10"))


def fetch_recent_uploads(handle=None, hours=None, youtube=None, max_results=None):
    """Recent uploads from the watched channel, newest first, as
    [{"title", "description", "published_at"}, ...].

    Uses channels().list + playlistItems().list (1 quota unit each) rather
    than search.list (100 units) — see topic_reach.py's docstring for why
    that distinction matters against a 10,000/day allowance shared with the
    channel's own uploads.

    Never raises — a missing handle, a dead quota or an auth failure
    returns [] and the caller moves on to the normal trend flow, same
    contract as trend_source.fetch_google_trends.
    """
    handle = handle if handle is not None else WATCH_CHANNEL_HANDLE
    if not handle:
        return []
    hours = hours if hours is not None else WATCH_LOOKBACK_HOURS
    max_results = max_results or WATCH_MAX_VIDEOS

    try:
        if youtube is None:
            from youtube_publisher import get_authenticated_service
            youtube = get_authenticated_service()

        ch = youtube.channels().list(part="contentDetails", forHandle=handle).execute()
        items = ch.get("items") or []
        if not items:
            print(f"⚠️ Channel watch: {handle!r} not found.")
            return []
        uploads_id = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]

        playlist = youtube.playlistItems().list(
            part="snippet", playlistId=uploads_id, maxResults=max_results).execute()
    except Exception as e:
        print(f"⚠️ Channel watch unavailable ({type(e).__name__}: {e}).")
        return []

    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours)
    out = []
    for it in playlist.get("items") or []:
        snip = it.get("snippet") or {}
        published = snip.get("publishedAt", "")
        try:
            when = datetime.datetime.strptime(
                published, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
        except ValueError:
            continue
        if when < cutoff:
            continue
        out.append({"title": snip.get("title", ""),
                    "description": snip.get("description", ""),
                    "published_at": published,
                    "video_id": (snip.get("resourceId") or {}).get("videoId", "")})

    # View counts give extract_channel_subjects a tiebreaker between
    # otherwise comparable stories. Not load-bearing: a failure here (a
    # dead quota, an auth hiccup) just means the model ranks on the
    # description text alone, same as if this block did not exist — it
    # never costs the run the candidates themselves.
    video_ids = [v["video_id"] for v in out if v["video_id"]]
    if video_ids:
        try:
            stats = youtube.videos().list(
                part="statistics", id=",".join(video_ids)).execute()
            counts = {it["id"]: int(it.get("statistics", {}).get("viewCount", 0))
                      for it in stats.get("items") or []}
            for v in out:
                if v["video_id"] in counts:
                    v["view_count"] = counts[v["video_id"]]
        except Exception as e:
            print(f"⚠️ Channel watch: view counts unavailable "
                  f"({type(e).__name__}: {e}).")

    return out


def get_watch_candidates(handle=None, hours=None, youtube=None, pool=None,
                         avoid_titles=()):
    """[{"subject", "score", "source"}, ...] in the same shape
    trend_source.rank_candidates produces, so it drops straight into the
    same select_historical_anchor call the normal trending flow uses.

    Every "subject" here is a generic label extract_channel_subjects
    produced, ranked most important first — never a video's own title or
    description (see the module docstring's safety boundary). [] when no
    recent upload clears the bar, which is the ordinary case, not an
    error: the caller falls through to the normal trend_source flow
    exactly as if this module did not exist.
    """
    videos = fetch_recent_uploads(handle=handle, hours=hours, youtube=youtube)
    if videos:
        print(f"📺 Channel watch: {len(videos)} recent upload(s) from "
              f"{handle or WATCH_CHANNEL_HANDLE!r}")

    from longform_llm import extract_channel_subjects
    candidates = extract_channel_subjects(videos, pool=pool,
                                          avoid_titles=avoid_titles)
    for c in candidates:
        print(f"   [{c['score']}] channel_watch {c['subject']!r}")
    if videos and not candidates:
        print("   No recent upload produced a usable subject.")
    return candidates


if __name__ == "__main__":
    for c in get_watch_candidates():
        print(c)
