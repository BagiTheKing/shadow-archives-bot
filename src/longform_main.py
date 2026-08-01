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

from longform_llm import generate_longform_script, flatten_scenes
from longform_visual import generate_longform_visuals
from longform_video import generate_longform_video, timestamp
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


def build_metadata(script, chapter_marks, total_duration):
    title = sanitize_title(script.get("title") or script.get("topic", "")[:90])

    parts = []
    if script.get("description"):
        parts.append(script["description"].strip())
    if script.get("topic"):
        parts.append(script["topic"].strip())

    chapters = build_chapter_block(chapter_marks, total_duration)
    if chapters:
        parts.append(chapters)

    parts.append(
        f"{CHANNEL_NAME} — one catastrophic decision, examined in full.\n"
        f"The 60-second version of this story is on the channel. "
        f"Subscribe for the next one."
    )
    parts.append("Note: visuals in this video are AI-generated illustrations of "
                 "real historical events.")

    description = "\n\n".join(parts)

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


def main():
    print(f"🚀 {CHANNEL_NAME} — long-form pipeline")
    cleanup_build_dir()

    # Phase 1: chaptered script
    script = generate_longform_script()
    if not script:
        print("❌ Halted: script generation failed.")
        raise SystemExit(1)

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

    # Phase 4: metadata + upload
    title, description, tags = build_metadata(script, chapter_marks, total)
    print(f"📋 Title: {title}")
    print(f"⏱️  Duration: {minutes:.1f} min | {len(chapter_marks)} chapters")

    if os.getenv("SKIP_UPLOAD") == "1":
        print("⏭️ SKIP_UPLOAD=1 — rendered but not uploaded (dry run).")
        print("--- description ---")
        print(description)
    else:
        upload_video_to_youtube(video_path, title, description, tags)

    print("🎉 Long-form pipeline complete.")


if __name__ == "__main__":
    main()
