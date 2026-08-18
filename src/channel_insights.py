"""
Channel insights — let the numbers pick the topic.

The encore lane expands whichever Short actually earned the most engagement,
rather than whatever the date rotation happens to land on. Likes are the signal
used, not views: Shorts views are heavily determined by how long the algorithm
chose to push a video, whereas a like is a deliberate act by a viewer who
watched it. On this channel the two rank differently, and likes track the videos
people finished.

Everything here degrades gracefully. If the credentials lack read scope, or the
API is down, it returns None and the caller falls back to the date rotation. A
missing insight must never cost a week's video.
"""

import os
import re

from topics import ALL_TOPICS, MIN_RATING_AGE_DAYS

STOPWORDS = {
    "the", "that", "this", "with", "from", "into", "when", "what", "which",
    "who", "why", "how", "and", "for", "was", "were", "had", "has", "his",
    "her", "its", "their", "shorts", "short", "history", "story", "video",
    "a", "an", "of", "in", "on", "at", "to", "by", "is", "it", "as", "one",
}


def _client():
    """Reuses the publisher's OAuth credentials."""
    from youtube_publisher import get_authenticated_service
    return get_authenticated_service()


def _stem(word):
    """Crude suffix stripping so 'sinks', 'sinking' and 'sunk' converge enough
    to overlap. Real stemming is not worth a dependency for this."""
    for suffix in ("ing", "ed", "es", "s"):
        if len(word) > len(suffix) + 3 and word.endswith(suffix):
            return word[:-len(suffix)]
    return word


# The channel's title formula, plus spoken numbers. These words are rare enough
# in the pool that rarity-weighting alone still trusts them, but they carry no
# information about WHICH story a title is: every title on the channel is "The
# <error> That <destroyed> <something>", and every topic entry describes a
# decision and a disaster. Matching on them produced real mistakes — "The Math
# Error That Starved Air Canada Flight 143" matched Ariane 5 on the word
# "flight", and "The Order That Killed Six Hundred" matched the Sultana on
# "six". Stemmed forms, because that is what the tokeniser emits.
FORMULA_WORDS = {
    "design", "destroy", "kill", "order", "mistake", "error", "decision",
    "decid", "flaw", "machine", "flight", "disaster", "failur", "fail",
    "wrong", "cost", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "hundred", "thousand", "million",
}


def _tokens(text):
    return {t for t in
            (_stem(w) for w in re.findall(r"[a-z]+", (text or "").lower())
             if len(w) > 2 and w not in STOPWORDS)
            if t not in FORMULA_WORDS}


# How many pool topics each token appears in. Every title on this channel is
# built from the same formula — "The <design flaw> That <destroyed> <thing>" —
# so words like "design", "destroyed" and "killed" are in most titles AND most
# topics, and matching on them is matching on nothing. Weighting each shared
# token by how rare it is in the pool fixes that: "hood" and "ariane" decide the
# match, "destroyed" barely moves it.
#
# The bug this replaces was real and silent: "The Design Flaw That Destroyed HMS
# Hood" matched the Tacoma Narrows Bridge (shared: design, destroyed) ahead of
# the HMS Hood entry (shared: hms, hood), so the Hood topic stayed unexcluded
# and was free to come round again as a duplicate.
_DOC_FREQ = {}
for _topic in ALL_TOPICS:
    for _t in {_stem(w) for w in re.findall(r"[a-z]+", _topic.lower())
               if len(w) > 2 and w not in STOPWORDS}:
        _DOC_FREQ[_t] = _DOC_FREQ.get(_t, 0) + 1


def _weight(token):
    """A token found in one topic is worth a full point; one found in twenty is
    worth a twentieth."""
    return 1.0 / _DOC_FREQ.get(token, 1)


# A single distinctive word ("Ariane", "Hood", "Slocum") is enough. Two
# formula words are not: "design" and "destroyed" together score around 0.2.
MATCH_FLOOR = 0.55


def match_topic(title: str, description: str = ""):
    """Maps a published video back to the topic that produced it.

    Preferred path: main.py writes the exact topic string into every description
    it publishes, so for anything published after that change this is an exact
    lookup rather than a guess.

    Fallback: token overlap against the pool, for the ~29 videos published
    before descriptions carried the topic.
    """
    if description:
        for topic in ALL_TOPICS:
            if topic.strip() and topic.strip() in description:
                return topic

    want = _tokens(title)
    if not want:
        return None
    best, best_score = None, 0.0
    for topic in ALL_TOPICS:
        have = _tokens(topic)
        if not have:
            continue
        overlap = want & have
        if not overlap:
            continue
        score = sum(_weight(t) for t in overlap)
        if score > best_score:
            best, best_score = topic, score
    return best if best_score >= MATCH_FLOOR else None


def fetch_recent_videos(limit=50):
    """Returns [{videoId, title, likes, views, seconds}] or None if unavailable."""
    try:
        yt = _client()
        ch = yt.channels().list(part="contentDetails", mine=True).execute()
        items = ch.get("items") or []
        if not items:
            print("⚠️ No channel returned for these credentials.")
            return None
        uploads = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]

        ids, page = [], None
        while len(ids) < limit:
            pl = yt.playlistItems().list(
                part="contentDetails", playlistId=uploads,
                maxResults=min(50, limit - len(ids)), pageToken=page).execute()
            ids += [i["contentDetails"]["videoId"] for i in pl.get("items", [])]
            page = pl.get("nextPageToken")
            if not page:
                break

        out = []
        for i in range(0, len(ids), 50):
            chunk = ids[i:i + 50]
            vids = yt.videos().list(part="snippet,statistics,contentDetails",
                                    id=",".join(chunk)).execute()
            for v in vids.get("items", []):
                stats = v.get("statistics", {})
                out.append({
                    "videoId": v["id"],
                    "title": v["snippet"]["title"],
                    # match_topic's exact-match path reads the topic line that
                    # main.py writes into every description. It was never
                    # given one, so the reliable path was dead code and every
                    # match fell through to fuzzy title overlap.
                    "description": v["snippet"].get("description", ""),
                    # Read by published_history's same-day duplicate guard.
                    "publishedAt": v["snippet"].get("publishedAt", ""),
                    "likes": int(stats.get("likeCount", 0) or 0),
                    "views": int(stats.get("viewCount", 0) or 0),
                    "duration": v["contentDetails"].get("duration", ""),
                })
        return out
    except Exception as e:
        print(f"⚠️ Channel insights unavailable ({type(e).__name__}: {e}).")
        if "insufficient" in str(e).lower() or "403" in str(e):
            # Scopes are frozen when a refresh token is minted, so this can
            # only be fixed by creating the token again — no amount of code
            # changes will grant it.
            print("   YT_REFRESH_TOKEN was minted without "
                  "'youtube.readonly', so this lane cannot read the channel's "
                  "own numbers and is falling back to the date rotation.")
            print("   Fix: run tools/get_youtube_refresh_token.py locally and "
                  "replace the YT_REFRESH_TOKEN secret.")
        else:
            print("   Falling back to the date rotation.")
        return None


def age_days(video) -> float:
    """How long a video has been live, or None when it does not say."""
    from published_history import _published_at
    when = _published_at(video)
    if not when:
        return None
    import datetime
    delta = datetime.datetime.now(datetime.timezone.utc) - when
    return delta.total_seconds() / 86400


def is_mature(video) -> bool:
    """Whether this upload has been live long enough for its numbers to mean
    anything. A one-day-old Short has few views because it is one day old, not
    because nobody wants the subject — reading the second thing into the first
    is how the Great Fire of London got rated SATURATED on a 24-hour-old video.

    Unknown age counts as mature: the ~29 uploads from before publishedAt was
    read back are all months old, and dropping them would cost more than it
    protects."""
    age = age_days(video)
    return age is None or age >= MIN_RATING_AGE_DAYS


def _is_short(duration_iso: str) -> bool:
    """PT#M#S under three minutes. Shorts are the only thing on the channel now,
    but this keeps the encore lane from selecting a previous long-form video."""
    m = re.match(r"PT(?:(\d+)M)?(?:(\d+)S)?$", duration_iso or "")
    if not m:
        return False
    mins = int(m.group(1) or 0)
    secs = int(m.group(2) or 0)
    return mins * 60 + secs <= 180


def best_performing_topic(min_likes=2, exclude=None):
    """The topic behind the most-liked Short. Returns (topic, meta) or None.

    Walks candidates in order, so if the top video's title cannot be matched
    back to a pool entry, the next one is tried rather than giving up.

    Anything the long-form side has already covered is skipped. Without that the
    lane is a repeat generator by construction: the most-liked Short does not
    change from week to week, so every run would expand the same story again.
    `exclude` adds to the topics read off the channel's own long-form uploads.
    """
    videos = fetch_recent_videos()
    if not videos:
        return None

    already = set(exclude or ())
    for v in videos:
        if not _is_short(v["duration"]):
            done = match_topic(v["title"], v.get("description", ""))
            if done:
                already.add(done)
    if already:
        print(f"🚫 {len(already)} topic(s) already covered in long form — skipping.")

    shorts = [v for v in videos if _is_short(v["duration"])]
    # Rank only what has settled. A Short published yesterday cannot be compared
    # with one published a month ago, in either direction — raw totals favour
    # the old one and views-per-hour favours the new one.
    settled = [v for v in shorts if is_mature(v)]
    if len(settled) < len(shorts):
        print(f"⏳ Ignoring {len(shorts) - len(settled)} Short(s) under "
              f"{MIN_RATING_AGE_DAYS} days old — too young to rank.")
    shorts = settled or shorts
    ranked = sorted(shorts, key=lambda v: (-v["likes"], -v["views"]))
    print(f"📊 {len(shorts)} Shorts examined; top by likes:")
    for v in ranked[:5]:
        print(f"   {v['likes']:>4} likes  {v['views']:>6} views  {v['title'][:62]}")

    for v in ranked:
        if v["likes"] < min_likes:
            break
        topic = match_topic(v["title"], v.get("description", ""))
        if not topic:
            print(f"   (no topic match for {v['title'][:50]!r}, trying next)")
            continue
        if topic in already:
            print(f"   (already covered in long form: {v['title'][:50]!r})")
            continue
        print(f"🏆 Encore pick: {v['title'][:70]!r} ({v['likes']} likes)")
        print(f"   -> {topic[:100]}")
        return topic, v

    print("⚠️ No most-liked Short could be matched to an uncovered topic.")
    return None
