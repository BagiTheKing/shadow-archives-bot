"""
Long-form pipeline entry point — the weekly deep dive.

Runs on its own schedule, separate from the daily Shorts pipeline, and covers a
story the Shorts channel already published. The Short poses the anomaly in 45
seconds; this reconstructs the decision that caused it. Aimed at search traffic,
which is the only lane a channel this size can win, because search does not care
how many subscribers you have.
"""

import json
import os
import re
import shutil

from longform_llm import (generate_longform_script, flatten_scenes,
                          select_historical_anchor, QuotaExhausted)
from longform_visual import generate_longform_visuals
from longform_video import generate_longform_video, timestamp
from thumbnail_engine import make_thumbnail, pick_source_scene
from audio_engine import generate_audio_pipeline
from youtube_publisher import upload_video_to_youtube

CHANNEL_NAME = os.getenv("CHANNEL_NAME", "The Archive Paradox")
BUILD_DIR = "build"

BASE_TAGS = ["history documentary", "disaster documentary", "engineering failure",
             "history explained", "what went wrong", "military history"]

# YouTube only renders chapters when the first mark is 00:00, there are at least
# three of them, and each is at least ten seconds long.
MIN_CHAPTERS = 3
MIN_CHAPTER_SECONDS = 10


def cleanup_build_dir(build_dir=BUILD_DIR):
    if os.path.exists(build_dir):
        shutil.rmtree(build_dir)
    os.makedirs(build_dir)


def sanitize_title(title: str) -> str:
    """YouTube: 100 chars max, no angle brackets. No '#shorts' here — this is a
    regular video and that tag would misfile it."""
    clean = re.sub(r"[<>]", "", title or "").strip()
    clean = re.sub(r"#\w+", "", clean).strip()
    if len(clean) > 100:
        clean = clean[:100].rsplit(" ", 1)[0].rstrip(".,;:")
    return clean


def build_chapter_block(chapter_marks, total_duration):
    """Renders the chapter list, or returns '' if YouTube would reject it."""
    if len(chapter_marks) < MIN_CHAPTERS:
        return ""
    bounds = [m[1] for m in chapter_marks] + [total_duration]
    for i in range(len(chapter_marks)):
        if bounds[i + 1] - bounds[i] < MIN_CHAPTER_SECONDS:
            print(f"⚠️ Chapter {chapter_marks[i][0]!r} is under "
                  f"{MIN_CHAPTER_SECONDS}s — omitting chapter markers.")
            return ""
    if chapter_marks[0][1] > 0.5:
        print("⚠️ First chapter does not start at 0:00 — omitting chapter markers.")
        return ""

    lines = ["Chapters:"]
    for heading, start in chapter_marks:
        lines.append(f"{timestamp(start)} {heading}")
    return "\n".join(lines)


def build_credit_block(scenes):
    """Attribution for archival imagery and stock footage. CC-BY imagery from
    Wikimedia Commons requires credit; public-domain imagery deserves it. Kept
    within a budget so the block can never crowd out the description itself
    (YouTube's hard ceiling is 5,000 characters)."""
    lines = []
    for s in scenes:
        for credit in (s.get("credit") or []):
            if credit not in lines:
                lines.append(credit)
    parts = []
    if lines:
        block, budget = ["Archival imagery:"], 1800
        for line in lines:
            if len(line) + 1 > budget:
                block.append("…and others via Wikimedia Commons.")
                break
            block.append(f"• {line}")
            budget -= len(line) + 1
        parts.append("\n".join(block))
    if any(s.get("video_path") for s in scenes):
        parts.append("Stock footage courtesy of Pexels.")
    return "\n\n".join(parts)


def build_metadata(script, chapter_marks, total_duration, scenes=None):
    title = sanitize_title(script.get("title") or script.get("topic", "")[:90])

    parts = []
    if script.get("description"):
        parts.append(script["description"].strip())
    if script.get("topic"):
        parts.append(script["topic"].strip())

    chapters = build_chapter_block(chapter_marks, total_duration)
    if chapters:
        parts.append(chapters)

    # Only claim a companion Short exists when the topic actually came from one.
    cross_promo = ("\nThe 60-second version of this story is on the channel."
                   if script.get("has_short") else "")
    parts.append(
        f"{CHANNEL_NAME} — one catastrophic decision, examined in full."
        f"{cross_promo}\nSubscribe for the next one."
    )

    scenes = scenes or []
    used_archival = any(s.get("credit") for s in scenes)
    if used_archival:
        parts.append("Note: this video combines archival photographs with "
                     "AI-generated illustrations of real historical events.")
    else:
        parts.append("Note: visuals in this video are AI-generated "
                     "illustrations of real historical events.")

    credit_block = build_credit_block(scenes)
    if credit_block:
        parts.append(credit_block)

    description = "\n\n".join(parts)
    if len(description) > 4950:          # YouTube rejects past 5,000
        description = description[:4950].rsplit("\n", 1)[0]

    tags = [t.strip() for t in (script.get("tags") or [])
            if isinstance(t, str) and t.strip()][:15]
    tags += BASE_TAGS
    tags = list(dict.fromkeys(tags))

    trimmed, budget = [], 500          # YouTube rejects tag lists over 500 chars
    for t in tags:
        if len(t) + 1 > budget:
            break
        trimmed.append(t)
        budget -= len(t) + 1

    return title, description, trimmed


def resolve_topic(lane: str):
    """Chooses this run's topic.
    Returns (topic, enforce_history, from_short, era_year).

    era_year is the story's approximate year when the lane knows it (the
    trending lane's anchor carries one). The script engine otherwise reads it
    out of the topic text; it drives the era gates on every visual.

    from_short records whether the topic really is one the channel has already
    posted a Short about. Only then may the description say so — on a fallback
    the rotation can hand back a topic that was never published, and the
    cross-promotion line would be a lie.

    Both lanes fall back to the date rotation, which always works offline. A
    failed insight or a dead trend feed must cost the run its *choice* of topic,
    never the video itself.
    """
    if lane == "encore":
        # Expand whichever Short actually earned the most likes.
        try:
            from channel_insights import best_performing_topic
            picked = best_performing_topic()
            if picked:
                return picked[0], False, True, None
        except Exception as e:
            print(f"⚠️ Encore selection failed ({type(e).__name__}: {e}).")
        print("↩️  Falling back to the date rotation.")
        return None, False, False, None

    if lane == "timely":
        try:
            from trend_source import get_trending_subjects
            region, candidates = get_trending_subjects()
            anchor = select_historical_anchor(candidates)
            if anchor:
                return anchor["topic"], True, False, anchor.get("anchor_year")
        except QuotaExhausted:
            # Falling back to the rotation here would be pointless: the chapter
            # calls are about to hit the same wall. Let it surface.
            raise
        except Exception as e:
            print(f"⚠️ Trending selection failed ({type(e).__name__}: {e}).")
        print("↩️  No anchor cleared the history rule — using the date rotation.")
        return None, False, False, None

    return None, False, False, None


def main():
    lane = os.getenv("LONGFORM_LANE", "encore").strip().lower()
    print(f"🚀 {CHANNEL_NAME} — long-form pipeline [lane: {lane}]")
    cleanup_build_dir()

    # Topic selection and script generation are covered by one handler. Anchor
    # selection is itself an LLM call, so it can hit the wall first — it did, and
    # the handler used to sit below it, letting the exception escape as a raw
    # traceback instead of the explanation it exists to print.
    try:
        topic, enforce_history, from_short, era_year = resolve_topic(lane)
        script = generate_longform_script(topic=topic,
                                          enforce_history=enforce_history,
                                          era_year=era_year)
    except QuotaExhausted as e:
        print(f"\n❌ Halted: {e}")
        print("   OpenRouter's free tier allows 50 requests a day across all "
              "free models, shared with every other run that day.")
        print("   Options: move this lane to a day with no other run, or add "
              "credits to raise the free-model daily limit to 1,000.")
        raise SystemExit(1)

    if not script:
        print("❌ Halted: script generation failed.")
        raise SystemExit(1)
    script["lane"] = lane
    script["has_short"] = from_short

    with open(os.path.join(BUILD_DIR, "longform_script.json"), "w",
              encoding="utf-8") as f:
        json.dump(script, f, indent=2, ensure_ascii=False)

    scenes = flatten_scenes(script)
    print(f"🎬 {len(scenes)} scenes across {len(script['chapters'])} chapters")

    # Phase 2: assets
    scenes = generate_audio_pipeline({"scenes": scenes}, BUILD_DIR)["scenes"]
    scenes = generate_longform_visuals(scenes, BUILD_DIR)

    usable = [s for s in scenes if s.get("image_path") and s.get("audio_path")]
    if len(usable) < 8:
        print(f"❌ Halted: only {len(usable)} usable scenes.")
        raise SystemExit(1)

    # Phase 3: render
    video_path, total, chapter_marks = generate_longform_video(scenes, BUILD_DIR)

    minutes = total / 60
    if minutes < 4:
        print(f"⚠️ Video is only {minutes:.1f} min — shorter than the 6-9 min target.")
    elif minutes > 12:
        print(f"⚠️ Video is {minutes:.1f} min — longer than the 6-9 min target.")

    # Phase 4: thumbnail
    thumb_path = None
    try:
        source = pick_source_scene(scenes)
        if source:
            thumb_path = os.path.join(BUILD_DIR, "thumbnail.jpg")
            make_thumbnail(source, script.get("thumbnail_text") or script["title"],
                           thumb_path, badge=script.get("thumbnail_badge", ""))
            print(f"🖼️  Thumbnail: {script.get('thumbnail_text')!r} "
                  f"({os.path.getsize(thumb_path) / 1024:.0f} KB)")
    except Exception as e:
        print(f"⚠️ Thumbnail generation failed ({type(e).__name__}: {e}) — "
              "YouTube will auto-select a frame.")
        thumb_path = None

    # Phase 5: metadata + upload
    title, description, tags = build_metadata(script, chapter_marks, total,
                                              scenes=scenes)
    print(f"📋 Title: {title}")
    print(f"⏱️  Duration: {minutes:.1f} min | {len(chapter_marks)} chapters")

    if os.getenv("SKIP_UPLOAD") == "1":
        print("⏭️ SKIP_UPLOAD=1 — rendered but not uploaded (dry run).")
        print("--- description ---")
        print(description)
    else:
        upload_video_to_youtube(video_path, title, description, tags,
                                thumbnail_path=thumb_path)

    print("🎉 Long-form pipeline complete.")


if __name__ == "__main__":
    main()
