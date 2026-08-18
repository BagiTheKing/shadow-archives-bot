"""
Measured saturation — is the feed already full of this story?

WHY THIS EXISTS
---------------
The reach ratings in topics.py are a prior: half measurement, half my judgement
about how heavily YouTube already covers a subject. Judgement is the weakest
part, and it goes stale — a topic nobody had touched in July can have thirty
versions by October. This measures it instead, on the channel's own credentials,
against the live catalogue, every run.

WHAT IT ACTUALLY COSTS (the part worth knowing before enabling it)
------------------------------------------------------------------
search.list is the most expensive call in the Data API: 100 quota units against
a default allowance of 10,000/day. videos.list is 1. So one vetted topic is 101
units and MAX_CHECKS caps a run at three, ~303 units — about 3% of the daily
allowance, shared with the uploads themselves (1,600 units each). That is
affordable; running it across all 62 pool topics would not be (6,262 units).
This is why vetting happens on the CANDIDATE at pick time, not on the pool.

TWO THINGS THE API WILL NOT DO, WHICH THE SCORING WORKS AROUND
--------------------------------------------------------------
1. search.list returns no statistics — no view counts, no likes. Counting
   "Shorts over 100k views" therefore needs a second call: search.list for the
   ids, then videos.list for the numbers.
2. There is no Shorts filter. videoDuration=short means UNDER FOUR MINUTES,
   which sweeps in ordinary short videos. The duration is re-checked against
   the real Shorts ceiling (3 minutes) after the statistics come back.

And pageInfo.totalResults is an estimate that changes between identical
requests, so nothing here counts it. Only returned, verified items are counted.

Everything degrades to "no opinion". A missing scope, an exhausted quota or a
dead API returns None and the caller keeps the static rating — a topic must
never be rejected because the check failed to run.
"""

import datetime
import os
import re

# How many high-performing Shorts on the same subject, published in the last
# year, make a topic not worth entering. Five of a top-25 sample means the feed
# already has better-funded versions ranked above anything this channel makes.
SATURATION_LIMIT = int(os.getenv("SATURATION_LIMIT", "5"))

# Views that count as "a version already winning".
BIG_VIEW_FLOOR = int(os.getenv("SATURATION_VIEW_FLOOR", "100000"))

# Topics vetted per run. Each one is ~101 quota units.
MAX_CHECKS = int(os.getenv("MAX_REACH_CHECKS", "3"))

LOOKBACK_DAYS = 365
SAMPLE_SIZE = 25
SHORTS_MAX_SECONDS = 180

# Set MEASURE_SATURATION=0 to run on the static ratings alone.
ENABLED = os.getenv("MEASURE_SATURATION", "1") == "1"


def search_query(topic: str) -> str:
    """The words a viewer would actually type, out of a pool entry.

    Pool topics are written as one long descriptive sentence — "The Ariane 5
    Flight 501 explosion (1996), destroyed 39 seconds after launch by reused
    software..." — and searching that whole string matches nothing. The subject
    is the part before the first comma or bracket.
    """
    head = re.split(r"[(,;:]", topic or "", maxsplit=1)[0].strip()
    head = re.sub(r"^(the|a|an)\s+", "", head, flags=re.IGNORECASE).strip()
    return " ".join(head.split()[:8])


def _iso_duration_seconds(iso: str) -> int:
    m = re.match(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$", iso or "")
    if not m:
        return 10 ** 6
    h, mi, sec = (int(g or 0) for g in m.groups())
    return h * 3600 + mi * 60 + sec


def measure(topic: str, youtube=None):
    """Returns {"query", "big", "sampled", "top_views"} or None if unavailable.

    "big" is the number of genuine Shorts on this subject, published in the last
    year, that are already over BIG_VIEW_FLOOR views.
    """
    if not ENABLED:
        return None
    query = search_query(topic)
    if not query:
        return None

    try:
        if youtube is None:
            from youtube_publisher import get_authenticated_service
            youtube = get_authenticated_service()

        after = (datetime.datetime.now(datetime.timezone.utc)
                 - datetime.timedelta(days=LOOKBACK_DAYS))
        found = youtube.search().list(
            part="id", q=query, type="video",
            videoDuration="short",          # under 4 minutes, NOT "is a Short"
            order="viewCount",
            publishedAfter=after.strftime("%Y-%m-%dT%H:%M:%SZ"),
            maxResults=SAMPLE_SIZE,
        ).execute()

        ids = [i["id"]["videoId"] for i in found.get("items", [])
               if i.get("id", {}).get("videoId")]
        if not ids:
            return {"query": query, "big": 0, "sampled": 0, "top_views": 0}

        stats = youtube.videos().list(part="statistics,contentDetails",
                                      id=",".join(ids)).execute()
        big, top = 0, 0
        for v in stats.get("items", []):
            seconds = _iso_duration_seconds(
                v.get("contentDetails", {}).get("duration", ""))
            if seconds > SHORTS_MAX_SECONDS:
                continue                    # a short video, but not a Short
            views = int(v.get("statistics", {}).get("viewCount", 0) or 0)
            top = max(top, views)
            if views >= BIG_VIEW_FLOOR:
                big += 1
        return {"query": query, "big": big, "sampled": len(ids), "top_views": top}

    except Exception as e:
        print(f"⚠️ Saturation check unavailable ({type(e).__name__}: {e}).")
        low = str(e).lower()
        if "quota" in low:
            print("   Data API quota is spent for today — the static reach "
                  "ratings in topics.py carry the run.")
        return None


def is_saturated(topic: str, youtube=None) -> bool:
    """True only on evidence. No measurement means no objection."""
    result = measure(topic, youtube)
    if not result:
        return False
    verdict = result["big"] >= SATURATION_LIMIT
    print(f"🔎 {result['query']!r}: {result['big']} Short(s) over "
          f"{BIG_VIEW_FLOOR:,} views in the last year "
          f"(of {result['sampled']} sampled, top {result['top_views']:,})"
          f"{' — SATURATED' if verdict else ''}")
    return verdict


def vet(pick, exclude, youtube=None):
    """Walks candidates until one is not measurably saturated.

    `pick` is called as pick(exclude) and returns a topic or None; rejected
    topics are added to `exclude` so the next call moves on. Returns
    (topic, rejected).
    """
    rejected = []
    exclude = set(exclude)
    for _ in range(max(1, MAX_CHECKS)):
        topic = pick(exclude)
        if not topic or not is_saturated(topic, youtube):
            return topic, rejected
        rejected.append(topic)
        exclude.add(topic)
    # Out of checks. Publishing the last candidate beats publishing nothing.
    topic = pick(exclude)
    if topic:
        print(f"ℹ️  {len(rejected)} candidate(s) rejected as saturated; "
              f"stopping the search here to stay inside the API quota.")
    return topic, rejected
