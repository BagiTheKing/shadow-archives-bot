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

So this module NEVER hands a video's title or description forward as a
candidate subject. It only ever extracts a fixed, pre-written generic
theme label (see THEMES below) when the video's DESCRIPTION contains
that theme's keywords — not the title, which the video's own title
never contributes to matching, only to the console log line so a run
is human-readable — and drops the video entirely if none match. Same
"subject, never a take" boundary trend_source.py already draws for
headlines, applied here to a talk show instead. The theme label is then
subject to every one of select_historical_anchor's existing hard rules
(settled pre-cutoff event only, no taking sides, and — added alongside
this file — never a topic centred on a named living private individual),
so nothing about a real person's health, relationships or unverified
conduct can reach a script even if the source video was mostly about
exactly that.
"""

import datetime
import os

# Off by default: set to a channel's @handle to enable. Piers Morgan
# Uncensored was the channel that prompted this feature.
WATCH_CHANNEL_HANDLE = os.getenv("WATCH_CHANNEL_HANDLE", "")
WATCH_LOOKBACK_HOURS = int(os.getenv("WATCH_LOOKBACK_HOURS", "24"))
WATCH_MAX_VIDEOS = int(os.getenv("WATCH_MAX_VIDEOS", "10"))

# Generic, pre-written theme labels only — see the safety boundary above.
# Keyed by keywords that must appear in the video's DESCRIPTION (not the
# title — see _extract_theme); each label names a real, settled historical
# angle THE FAULT LINE can actually tell (see topics.py's _FAULTLINE and
# select_historical_anchor's worked examples), never the live show's own
# framing of the argument.
#
# An earlier draft of this list had six entries and gave religion exactly
# one bucket, Islam — too narrow to be useful, and a real editorial bias in
# its own right on a channel that has no reason to single out one faith.
# This version does two things about that: every major religion gets the
# same treatment, and US domestic politics — where a talk show like this
# actually spends most of its runtime, not on foreign policy — gets proper
# coverage instead of one immigration bucket.
THEMES = [
    # Religion and politics — no religion gets a bucket the others don't.
    (("muslim", "islam", "islamic", "sharia", "mosque", "hijab", "trojan horse",
      "caliphate"), "Islam and Western politics"),
    (("evangelical", "christian nationalis", "megachurch", "christian right",
      "bible belt"), "Christianity and American politics"),
    (("antisemit", "synagogue", "jewish community", "zionis"),
     "Judaism, antisemitism and politics"),
    (("hindu nationalis", "hindutva"), "religious nationalism in South Asia"),
    (("separation of church and state", "establishment clause",
      "religious freedom law", "religious liberty"),
     "religion and the American constitution"),

    # US domestic politics — the actual gap in the old list.
    (("election fraud", "voter id", "swing state", "electoral college",
      "gerrymander", "midterm election"), "US elections and voting rights"),
    (("abortion", "roe v wade", "roe vs wade", "pro-life", "pro-choice",
      "reproductive rights"), "abortion policy in America"),
    (("gun control", "second amendment", "mass shooting", "assault weapon",
      "gun violence"), "gun policy in America"),
    (("immigra", "migrant", "asylum", "border crossing", "deportation",
      "border wall", "sanctuary city"), "US immigration policy"),
    (("free speech", "censorship", "cancel culture", "first amendment",
      "content moderation", "deplatform"),
     "free speech and censorship in America"),
    (("police reform", "defund the police", "mass incarceration",
      "criminal justice reform", "police brutality"),
     "policing and criminal justice in America"),
    (("supreme court", "scotus", "constitutional amendment",
      "judicial nominee"), "the US Supreme Court and constitutional law"),
    (("obamacare", "affordable care act", "medicare", "medicaid",
      "healthcare reform", "drug prices"), "US healthcare policy"),
    (("government shutdown", "debt ceiling", "national debt",
      "federal budget"), "the US national debt and government shutdowns"),
    (("opioid crisis", "fentanyl", "drug decriminalization",
      "war on drugs"), "drug policy and the opioid crisis"),
    (("capitol riot", "election certification", "peaceful transfer of power",
      "insurrection"), "the peaceful transfer of power in America"),
    (("impeachment", "articles of impeachment"),
     "presidential impeachment in America"),

    # Foreign policy.
    (("israel", "gaza", "palestin", "hamas", "west bank", "idf", "hezbollah"),
     "the Israel-Palestine conflict"),
    (("ukrain", "russia", "putin", "zelensky", "kremlin", "crimea"),
     "the Russia-Ukraine war"),
    (("iran", "tehran", "ayatollah", "irgc", "nuclear deal"),
     "Iran and the West"),
    (("north korea", "pyongyang", "kim jong"), "North Korea and the West"),
    (("kashmir", "india-pakistan", "line of control"),
     "India, Pakistan and Kashmir"),
    (("nato", "taiwan", "south china sea"), "great-power rivalry"),

    # Economy.
    (("inflation", "interest rates", "federal reserve", "recession",
      "tariff", "trade war"), "inflation and economic policy"),
]


def _extract_theme(description: str):
    """A fixed label from THEMES, or None. Never returns anything derived
    from the video's own text — see the module docstring.

    Description only, not the title — titles on a show like this are
    written to be provocative shorthand ("Mehdi DESTROYS Nancy Mace!") and
    match on name and tone rather than subject; the description is where
    the actual topic of the segment gets spelled out.
    """
    text = (description or "").lower()
    for keywords, label in THEMES:
        if any(k in text for k in keywords):
            return label
    return None


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
                    "published_at": published})
    return out


def get_watch_candidates(handle=None, hours=None, youtube=None):
    """[{"subject", "score", "source"}, ...] in the same shape
    trend_source.rank_candidates produces, so it drops straight into the
    same select_historical_anchor call the normal trending flow uses.

    Every "subject" here is one of the fixed THEMES labels — never a
    video's own title or description (see the module docstring's safety
    boundary). [] when no recent upload matches a known theme, which is
    the ordinary case, not an error: the caller falls through to the
    normal trend_source flow exactly as if this module did not exist.
    """
    videos = fetch_recent_uploads(handle=handle, hours=hours, youtube=youtube)
    if videos:
        print(f"📺 Channel watch: {len(videos)} recent upload(s) from "
              f"{handle or WATCH_CHANNEL_HANDLE!r}")

    seen, out = set(), []
    for v in videos:
        theme = _extract_theme(v["description"])
        if not theme or theme in seen:
            continue
        seen.add(theme)
        out.append({"subject": theme, "score": 5, "source": "channel_watch"})
        print(f"   [5] channel_watch {theme!r} (from {v['title'][:70]!r})")
    if videos and not out:
        print("   No recent upload matched a known theme.")
    return out


if __name__ == "__main__":
    for c in get_watch_candidates():
        print(c)
