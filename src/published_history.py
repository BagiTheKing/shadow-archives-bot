"""
Published-history memory.

THE PROBLEM THIS SOLVES
-----------------------
The pipeline had no memory of what it had already published. GitHub Actions
wipes the filesystem between runs and topic selection is purely date-based, so
any second run on the same day — a manual trigger next to the scheduled one —
regenerated the same subject with a reworded title. That is not a theory; it is
what the channel's own upload history shows:

    2026-07-05 12:19  the mistake that killed the light brigade          1,059
    2026-07-05 13:35  The Ambiguous Order That Sent 673 Men Into Cannons     16
    2026-07-05 16:32  The Ambiguous Order That Sent 600 Into Cannons        377
    2026-08-07 14:02  The software error that destroyed the Ariane 5       100
    2026-08-07 16:25  The Reused Code That Destroyed Ariane 5 Flight 501   529
    2026-08-17 14:12  The Arrogant Mayor Who Burned London                    8
    2026-08-17 15:54  The Mayor's Dismissal That Burned London               25

Three pairs, each a couple of hours apart, each the second copy performing far
worse than the first. The channel is the source of truth about what it has
published, so this module reads it back before anything is generated.

Everything here degrades gracefully. No credentials, no read scope, or a dead
API returns None, and the caller keeps its old date-rotation behaviour — a
missing insight must never cost a run. The one thing it will not do is guess:
an unknown history means no same-day guard and no episode number, never a
fabricated one.
"""

import datetime
import os
import re

# How recently another upload has to have happened for this run to stand down.
# Shorts publish at most once a day (cron 15:23 UTC, Mon/Wed/Fri/Sat/Sun), so a
# 20-hour window catches a manual trigger sitting next to the scheduled run
# without ever blocking the next day's legitimate one.
GUARD_HOURS = float(os.getenv("DUPLICATE_GUARD_HOURS", "20"))

# Set FORCE_PUBLISH=1 to override the guard deliberately — e.g. re-running after
# a run that failed *after* uploading, or backfilling a missed day.
FORCE = os.getenv("FORCE_PUBLISH") == "1"


def load_published(limit: int = 60):
    """Recent uploads with titles, descriptions and publish times, or None."""
    try:
        from channel_insights import fetch_recent_videos
        return fetch_recent_videos(limit=limit)
    except Exception as e:
        print(f"⚠️ Published history unavailable ({type(e).__name__}: {e}).")
        return None


def _published_at(video):
    raw = (video or {}).get("publishedAt") or ""
    try:
        return datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def published_within(videos, hours: float = None):
    """Uploads newer than `hours`, newest first. The same-day duplicate guard."""
    hours = GUARD_HOURS if hours is None else hours
    cutoff = (datetime.datetime.now(datetime.timezone.utc)
              - datetime.timedelta(hours=hours))
    recent = [(t, v) for v in (videos or [])
              for t in [_published_at(v)] if t and t >= cutoff]
    return [v for _, v in sorted(recent, key=lambda p: p[0], reverse=True)]


def covered_topics(videos, longform_only: bool = False):
    """Pool topics the channel has already published.

    Two paths, both already used by the encore lane: an exact match against the
    topic line main.py writes into every description, and token overlap against
    the title for anything published before that. A false positive here only
    costs one topic its turn in the rotation, so the fuzzy path is worth having.
    """
    from channel_insights import match_topic, _is_short

    found = set()
    for v in videos or []:
        if longform_only and _is_short(v.get("duration", "")):
            continue
        topic = match_topic(v.get("title", ""), v.get("description", ""))
        if topic:
            found.add(topic)
    return found


def recent_titles(videos, limit: int = 30):
    """The last `limit` titles, newest first — handed to the writer so it does
    not reword a subject it has already covered."""
    dated = [(t, v.get("title", "")) for v in (videos or [])
             for t in [_published_at(v)] if t]
    dated.sort(key=lambda p: p[0], reverse=True)
    titles = [title for _, title in dated if title]
    if not titles:                     # no timestamps: fall back to API order
        titles = [v.get("title", "") for v in (videos or []) if v.get("title")]
    return titles[:limit]


def next_episode_number(videos, series_name: str):
    """The next number in the series, read off the channel itself.

    Returns None when the history could not be read. A wrong number is worse
    than no number — "#3" appearing twice reads as a broken channel — so the
    caller publishes an unnumbered title rather than guessing.
    """
    if videos is None or not series_name:
        return None
    # Every form the channel has published under: "#14" for the first fifty,
    # "No. 14" from here on. The count must not restart when the form changes.
    pattern = re.compile(
        rf"{re.escape(series_name)}\s*(?:#|No\.?\s*|Episode\s*)(\d+)",
        re.IGNORECASE)
    numbers = [int(m.group(1)) for v in videos
               for m in [pattern.search(v.get("title", "") or "")] if m]
    return max(numbers) + 1 if numbers else 1


def series_suffix(series_name: str, number) -> str:
    """` — Catastrophe Files No. 14`, or an unnumbered suffix, or nothing.

    A named, numbered series is still the cheapest subscribe reason there is —
    that part of the diagnosis stands. What was wrong was where it sat.

    As a PREFIX it spent the first 24 characters of every title, and 24
    characters is roughly everything the Shorts feed shows before it truncates.
    Fifty videos went out whose visible title was "Catastrophe Files #12 — The
    Mist...": identical on every one, with the subject cut off. The series
    marker was doing its job in the one position where nobody could use it, and
    hiding the only words that distinguish one video from the next.

    At the end it says the same thing to anyone who reads that far, and the
    story gets the space that decides the click. next_episode_number searches
    the whole title rather than anchoring, so the numbering carries across the
    change without a gap.

    "No. 14" rather than "#14" because YouTube reads a "#" anywhere in a title
    or description as a hashtag, and the first three it finds are rendered above
    the video as its category. An episode number is not a category, and it was
    arriving ahead of the three the channel actually chose.
    """
    if not series_name:
        return ""
    return f" — {series_name} No. {number}" if number else f" — {series_name}"


def describe(videos) -> str:
    if videos is None:
        return ("no published history this run — the duplicate guard and the "
                "episode number are both unavailable")
    return f"{len(videos)} published videos read back from the channel"
