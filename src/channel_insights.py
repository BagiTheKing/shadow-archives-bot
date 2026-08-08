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

from topics import ALL_TOPICS

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


def _tokens(text):
    return {_stem(w) for w in re.findall(r"[a-z]+", (text or "").lower())
            if len(w) > 2 and w not in STOPWORDS}


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
        overlap = len(want & have)
        if not overlap:
            continue
        # Normalised against the title, so a long topic entry is not favoured
        # purely for containing more words.
        score = overlap / max(3, len(want)) + overlap * 0.05
        if score > best_score:
            best, best_score = topic, score
    # Two solid content words in common is the floor for a confident match.
    return best if best_score >= 0.34 else None


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


def _is_short(duration_iso: str) -> bool:
    """PT#M#S under three minutes. Shorts are the only thing on the channel now,
    but this keeps the encore lane from selecting a previous long-form video."""
    m = re.match(r"PT(?:(\d+)M)?(?:(\d+)S)?$", duration_iso or "")
    if not m:
        return False
    mins = int(m.group(1) or 0)
    secs = int(m.group(2) or 0)
    return mins * 60 + secs <= 180


def best_performing_topic(min_likes=2):
    """The topic behind the most-liked Short. Returns (topic, meta) or None.

    Walks candidates in order, so if the top video's title cannot be matched
    back to a pool entry, the next one is tried rather than giving up.
    """
    videos = fetch_recent_videos()
    if not videos:
        return None

    shorts = [v for v in videos if _is_short(v["duration"])]
    ranked = sorted(shorts, key=lambda v: (-v["likes"], -v["views"]))
    print(f"📊 {len(shorts)} Shorts examined; top by likes:")
    for v in ranked[:5]:
        print(f"   {v['likes']:>4} likes  {v['views']:>6} views  {v['title'][:62]}")

    for v in ranked:
        if v["likes"] < min_likes:
            break
        topic = match_topic(v["title"], v.get("description", ""))
        if topic:
            print(f"🏆 Encore pick: {v['title'][:70]!r} ({v['likes']} likes)")
            print(f"   -> {topic[:100]}")
            return topic, v
        print(f"   (no topic match for {v['title'][:50]!r}, trying next)")

    print("⚠️ No most-liked Short could be matched to a topic.")
    return None
