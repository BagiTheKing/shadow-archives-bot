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

import formats

from caption_track import SRT_NAME
from longform_llm import (generate_longform_script, flatten_scenes,
                          select_historical_anchor, QuotaExhausted)
from longform_visual import generate_longform_visuals
from longform_video import generate_longform_video, timestamp
from thumbnail_engine import make_thumbnail, pick_source_scene
from audio_engine import generate_audio_pipeline
from published_history import (covered_topics, describe, load_published,
                               recent_titles)
from topics import pick_topic
from youtube_publisher import upload_video_to_youtube

# main.py (Shorts) has carried this exact protection since 19 August, after a
# manual run stacked on the day's scheduled one found Cloudflare's 10,000-
# neuron free allocation already spent, quietly rendered every frame on the
# weaker fallback, and published with nothing in the log saying so. This
# pipeline had no equivalent: a real run got 0/47 generated frames from
# Workers AI for the same reason and published anyway. Same env var as
# Shorts on purpose — one knob controls both.
MIN_PREFERRED_FRAMES = float(os.getenv("MIN_PREFERRED_FRAMES", "0.6"))

CHANNEL_NAME = os.getenv("CHANNEL_NAME", "The Archive Paradox")

# Same series as the Shorts, so the two formats read as one channel. It stays
# out of the long-form TITLE on purpose: these videos are aimed at search, and
# a prefix pushes the searchable words past where YouTube truncates them.
SERIES_NAME = os.getenv("SERIES_NAME", "Catastrophe Files")
BUILD_DIR = "build"

# Both formats file into the same playlist deliberately. A viewer who finishes a
# Short is handed the long-form telling of the next story rather than a
# same-length clip, which is the only route this channel has to session time.
PLAYLIST_DESCRIPTION = (
    f"{SERIES_NAME} — one catastrophic decision, explained. "
    "Every episode is a real event, a real choice, and what it cost."
)

# Format-NEUTRAL only — formats.tags_of supplies "military history" and
# "engineering failure" to the episodes those actually describe. Leaving them
# here put both on every video regardless of what it was about.
BASE_TAGS = ["history documentary", "disaster documentary",
             "history explained", "what went wrong"]

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


def check_visual_quality(workers_ai_generated: int, total_generated: int) -> None:
    """Halts before the render when the generated frames did not come from
    the good generator. Ported from main.py's check_visual_quality — see the
    comment on MIN_PREFERRED_FRAMES above for why.

    Only applies when Workers AI is actually configured, and only over the
    AI-GENERATED frames — archival photos and b-roll are real content, not a
    Cloudflare-quota fallback, and scoring them against this floor would
    punish a video for using MORE real imagery, not less.
    """
    from visual_engine import cloudflare_available
    if not cloudflare_available() or MIN_PREFERRED_FRAMES <= 0:
        return
    if not total_generated:
        return  # every frame was archival or b-roll — nothing to gate on

    share = workers_ai_generated / total_generated
    if share >= MIN_PREFERRED_FRAMES:
        return

    print(f"\n❌ Halted: only {workers_ai_generated}/{total_generated} generated "
          f"frames came from Workers AI (floor is {MIN_PREFERRED_FRAMES:.0%}).")
    print("   Cloudflare's free allocation is 10,000 neurons a day and resets at "
          "00:00 UTC, shared across every lane — Shorts, Timely and Encore all "
          "draw on the same allowance.")
    print("   Publishing this would put a visibly worse video on the channel "
          "under the same title and thumbnail as a good one.")
    print("   Wait for the reset, or set MIN_PREFERRED_FRAMES=0 to publish anyway.")
    raise SystemExit(1)


def build_metadata(script, chapter_marks, total_duration, scenes=None,
                   music_attribution=""):
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
    fmt = formats.get(script.get("format"))
    series_line = (f"\n{SERIES_NAME} · {fmt['name']} — {fmt['promise']}."
                   "\nIn full length here, and in 60 seconds on Shorts."
                   if SERIES_NAME else "")
    parts.append(
        f"{CHANNEL_NAME} — one catastrophic decision, examined in full."
        f"{cross_promo}{series_line}\nSubscribe for the next one."
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

    # Required, not optional, whenever it's non-empty: a Creative Commons
    # track (e.g. Kevin MacLeod) only stays license-compliant if this exact
    # credit text is in the description. See music_attribution.py.
    if (music_attribution or "").strip():
        parts.append(music_attribution.strip())

    description = "\n\n".join(parts)
    if len(description) > 4950:          # YouTube rejects past 5,000
        description = description[:4950].rsplit("\n", 1)[0]

    tags = [t.strip() for t in (script.get("tags") or [])
            if isinstance(t, str) and t.strip()][:15]
    tags += formats.tags_of(script.get("format"))
    tags += BASE_TAGS
    trimmed = formats.sanitize_tags(tags)

    return title, description, trimmed


def build_first_comment(script: dict) -> str:
    """Same reasoning as the Shorts side: the channel has never asked a
    question, and it has never received an answer."""
    question = (script.get("comment_question") or "").strip()
    if question:
        return question
    subject = (script.get("topic") or "").split("(")[0].strip(" ,.")
    if not subject:
        return ""
    return (f"{subject} — who do you hold responsible, and at which point was "
            "it already too late?")


def resolve_topic(lane: str, history=None):
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
    never the video itself. The fallback now skips anything already covered in
    long form, so a fallback week cannot quietly remake last week's video.
    """
    covered = covered_topics(history, longform_only=True) if history else set()
    if covered:
        print(f"📕 {len(covered)} topic(s) already covered in long form.")

    def fallback():
        offset = int(os.getenv("LONGFORM_TOPIC_OFFSET", "-7"))
        return pick_topic(offset, exclude=covered)

    if lane == "encore":
        # Expand whichever Short actually earned the most likes.
        try:
            from channel_insights import best_performing_topic
            picked = best_performing_topic(exclude=covered)
            if picked:
                return picked[0], False, True, None
        except Exception as e:
            print(f"⚠️ Encore selection failed ({type(e).__name__}: {e}).")
        print("↩️  Falling back to the date rotation.")
        return fallback(), False, False, None

    if lane == "timely":
        try:
            from trend_source import get_trending_subjects
            region, candidates = get_trending_subjects()
            anchor = select_historical_anchor(
                candidates, avoid_titles=recent_titles(history, 20))
            if anchor:
                return anchor["topic"], True, False, anchor.get("anchor_year")
        except QuotaExhausted:
            # Falling back to the rotation here would be pointless: the chapter
            # calls are about to hit the same wall. Let it surface.
            raise
        except Exception as e:
            print(f"⚠️ Trending selection failed ({type(e).__name__}: {e}).")
        print("↩️  No anchor cleared the history rule — using the date rotation.")
        return fallback(), False, False, None

    return fallback(), False, False, None


def main():
    lane = os.getenv("LONGFORM_LANE", "encore").strip().lower()
    print(f"🚀 {CHANNEL_NAME} — long-form pipeline [lane: {lane}]")
    cleanup_build_dir()

    # Topic selection and script generation are covered by one handler. Anchor
    # selection is itself an LLM call, so it can hit the wall first — it did, and
    # the handler used to sit below it, letting the exception escape as a raw
    # traceback instead of the explanation it exists to print.
    history = load_published()
    print(f"🧾 History: {describe(history)}")

    try:
        topic, enforce_history, from_short, era_year = resolve_topic(lane, history)
        if not topic:
            print("❌ Halted: every topic in the pool is already published. "
                  "Add new entries to CORE_TOPICS in src/topics.py.")
            raise SystemExit(1)
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

    # Asked out loud at the end, not only in the comment field. Same reasoning
    # as the Shorts pipeline: an unpinned comment sits below the fold.
    first_comment = build_first_comment(script)
    if first_comment and scenes and os.getenv("SPEAK_QUESTION", "1") == "1":
        scenes[-1]["narration"] = (
            f"{(scenes[-1].get('narration') or '').rstrip()} {first_comment}".strip())
        print(f"🗣️  Question added to the closing narration: {first_comment}")

    # Phase 2: assets
    scenes = generate_audio_pipeline({"scenes": scenes}, BUILD_DIR)["scenes"]
    scenes, workers_ai_generated, total_generated = generate_longform_visuals(
        scenes, BUILD_DIR)

    usable = [s for s in scenes if s.get("image_path") and s.get("audio_path")]
    if len(usable) < 8:
        print(f"❌ Halted: only {len(usable)} usable scenes.")
        raise SystemExit(1)

    # Before spending the render, not after: a video nobody should publish is
    # not worth an hour of Actions time either.
    check_visual_quality(workers_ai_generated, total_generated)

    # Phase 3: render
    video_path, total, chapter_marks, music_attribution = generate_longform_video(
        scenes, BUILD_DIR)

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
            year = (script.get("thumbnail_badge") or "").strip()
            name = formats.name_of(script.get("format"))
            badge = f"{name} · {year}" if year else name
            make_thumbnail(source, script.get("thumbnail_text") or script["title"],
                           thumb_path, badge=badge,
                           accent=formats.accent_of(script.get("format")))
            print(f"🖼️  Thumbnail: {script.get('thumbnail_text')!r} [{badge}] "
                  f"({os.path.getsize(thumb_path) / 1024:.0f} KB)")
    except Exception as e:
        print(f"⚠️ Thumbnail generation failed ({type(e).__name__}: {e}) — "
              "YouTube will auto-select a frame.")
        thumb_path = None

    # Phase 5: metadata + upload
    title, description, tags = build_metadata(script, chapter_marks, total,
                                              scenes=scenes,
                                              music_attribution=music_attribution)
    print(f"📋 Title: {title}")
    print(f"🏷️  Tags ({len(tags)}): {tags}")
    print(f"⏱️  Duration: {minutes:.1f} min | {len(chapter_marks)} chapters")

    caption_path = os.path.join(BUILD_DIR, SRT_NAME)
    if not os.path.exists(caption_path):
        caption_path = None

    if os.getenv("SKIP_UPLOAD") == "1":
        print("⏭️ SKIP_UPLOAD=1 — rendered but not uploaded (dry run).")
        print("--- description ---")
        print(description)
    else:
        upload_video_to_youtube(video_path, title, description, tags,
                                thumbnail_path=thumb_path,
                                first_comment=first_comment,
                                caption_path=caption_path,
                                playlist_title=SERIES_NAME,
                                playlist_description=PLAYLIST_DESCRIPTION)

    print("🎉 Long-form pipeline complete.")


if __name__ == "__main__":
    main()
