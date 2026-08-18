import json
import os
import re
import shutil

from llm_engine import generate_video_script
from topics import pick_topic
from published_history import (FORCE, GUARD_HOURS, covered_topics, describe,
                               load_published, next_episode_number,
                               published_within, recent_titles, series_prefix)
from topic_reach import vet
from audio_engine import generate_audio_pipeline
from visual_engine import generate_visual_pipeline
from video_engine import generate_video_pipeline
from youtube_publisher import upload_video_to_youtube


def cleanup_build_dir(build_dir="build"):
    if os.path.exists(build_dir):
        shutil.rmtree(build_dir)
    os.makedirs(build_dir)


SUFFIX = " #shorts"


def title_budget(prefix: str = "") -> int:
    """Characters left for the story title itself. YouTube's ceiling is 100 and
    both the series prefix and the #shorts suffix are spent out of it."""
    return 100 - len(SUFFIX) - len(prefix)


def sanitize_title(title: str, prefix: str = "") -> str:
    """YouTube: max 100 chars, no angle brackets. Cut on a word boundary."""
    clean = re.sub(r"[<>]", "", title or "").strip()
    max_body = title_budget(prefix)
    if len(clean) > max_body:
        clean = clean[:max_body].rsplit(" ", 1)[0].rstrip(".,;:")
    return prefix + clean + SUFFIX


# The channel is "The Archive Paradox". The pipeline used to write "The Shadow
# Archives" into every description, so every video published so far has asked
# viewers to subscribe to a channel that does not exist. Single source of truth:
CHANNEL_NAME = os.getenv("CHANNEL_NAME", "The Archive Paradox")

# Stated in the description so the promise matches what the schedule delivers.
UPLOAD_CADENCE = os.getenv("UPLOAD_CADENCE", "five days a week")

# The series name and number are the channel's only recurring format. Nothing
# published so far tells a viewer there is a next one, which is most of why
# 9,000 views converted 25 subscribers. Set SERIES_NAME="" to publish untitled.
SERIES_NAME = os.getenv("SERIES_NAME", "Catastrophe Files")

# Broad tags are close to worthless on their own, but they cost nothing and the
# specific ones from the script are what give the video a chance in search.
BASE_TAGS = ["history", "shorts", "history shorts", "military history",
             "historical disasters", "engineering failures", "documentary"]


def build_metadata(script_data: dict, episode=None):
    prefix = series_prefix(SERIES_NAME, episode)
    title = sanitize_title(script_data.get("title")
                           or script_data["scenes"][0]["narration"], prefix)

    description = script_data.get("description", "").strip()

    # Restating the subject in plain words gives the description something
    # indexable — right now 96% of views come from the Shorts feed alone.
    topic = (script_data.get("topic") or "").strip()
    if topic:
        description += f"\n\n{topic}"

    series_line = ""
    if SERIES_NAME:
        numbered = f" #{episode}" if episode else ""
        series_line = (f"\n\n{SERIES_NAME}{numbered} — every episode is one "
                       "decision and what it cost.")
    description += series_line
    description += (
        f"\n\n{CHANNEL_NAME} — one catastrophic decision, explained, {UPLOAD_CADENCE}."
        f"\nSubscribe if you want the next one."
        "\n\nNote: visuals in this video are AI-generated illustrations of real"
        " historical events.\n#shorts #history #disaster"
    )

    tags = script_data.get("tags") or []
    tags = [t.strip() for t in tags if isinstance(t, str) and t.strip()][:15]
    tags += BASE_TAGS
    tags = list(dict.fromkeys(tags))  # dedupe, keep order

    # YouTube rejects the request if the tag list exceeds 500 characters.
    trimmed, budget = [], 500
    for t in tags:
        cost = len(t) + 1
        if cost > budget:
            break
        trimmed.append(t)
        budget -= cost

    return title, description, trimmed


# The question is asked out loud at the end of the video, not only under it.
# An unpinned channel comment sits below the fold and nobody scrolls to it; the
# last two seconds of the video are the only place every viewer is guaranteed to
# be. Set SPEAK_QUESTION=0 if retention drops — the narration is what pays for
# everything else, so this is the one knob worth watching.
SPEAK_QUESTION = os.getenv("SPEAK_QUESTION", "1") == "1"


def append_question_to_script(script_data: dict) -> str:
    """Adds the question to the last scene's narration, in place.

    Deliberately after validation, never before: the hook rules and the 130-150
    word budget are checked against what the writer produced, and a question
    bolted on afterwards must not push a good script out of range.

    Going through the narration — rather than drawing a separate end card — is
    what puts it on screen as well as in the audio. Captions are word-synced
    from narration, so the spoken question is subtitled automatically, over the
    same seconds the subscribe overlay is already using.
    """
    question = build_first_comment(script_data)
    if not (SPEAK_QUESTION and question):
        return question
    scenes = script_data.get("scenes") or []
    if not scenes:
        return question
    last = scenes[-1]
    narration = (last.get("narration") or "").rstrip()
    if question.lower() in narration.lower():
        return question
    last["narration"] = f"{narration} {question}".strip()
    print(f"🗣️  Question added to the final scene: {question}")
    return question


def build_first_comment(script_data: dict) -> str:
    """The question posted under the video. Falls back to naming the subject,
    because a generic prompt is still better than the silence the channel has
    now — but the model is asked for something specific first."""
    question = (script_data.get("comment_question") or "").strip()
    if not question:
        subject = (script_data.get("topic") or "").split("(")[0].strip(" ,.")
        if not subject:
            return ""
        question = (f"{subject} — at which moment do you think it stopped "
                    "being recoverable?")
    return question


def plan_run():
    """Reads the channel back before writing anything.

    Returns (topic, avoid_titles, episode). Raises SystemExit(0) — a clean stop,
    not a failure — when another upload already went out inside the guard window,
    because a second video on the same day is the one thing this channel has
    proven it must not do again.
    """
    history = load_published()
    print(f"🧾 History: {describe(history)}")

    if history is None:
        # No memory this run. The rotation still varies by date, so a scheduled
        # run is fine; what is unavailable is protection against a same-day
        # re-run. Say so loudly rather than pretending the guard ran.
        print("⚠️ Duplicate guard INACTIVE — cannot see what is already "
              "published. Do not trigger this workflow manually today.")
        return pick_topic(), [], None

    recent = published_within(history)
    if recent and not FORCE:
        newest = recent[0]
        print(f"🛑 {len(recent)} upload(s) in the last {GUARD_HOURS:g}h — "
              f"most recently {newest.get('title', '')[:70]!r}.")
        print("   Stopping before generation. Two videos hours apart is what "
              "produced the duplicate clusters, so this run stands down.")
        print("   Set FORCE_PUBLISH=1 to override deliberately.")
        raise SystemExit(0)
    if recent and FORCE:
        print(f"⚠️ FORCE_PUBLISH=1 — publishing despite {len(recent)} upload(s) "
              f"in the last {GUARD_HOURS:g}h.")

    used = covered_topics(history)
    print(f"📕 {len(used)} pool topic(s) already published — excluded.")

    # The static reach ratings are a prior; this checks the live catalogue.
    # A topic the feed is already full of is skipped for the next candidate.
    topic, rejected = vet(lambda excl: pick_topic(exclude=excl), used)
    for r in rejected:
        print(f"   ↩︎ skipped as saturated: {r[:70]}")

    episode = next_episode_number(history, SERIES_NAME)
    return topic, recent_titles(history), episode


def main():
    print(f"🚀 Initiating the {CHANNEL_NAME} pipeline...")
    build_dir = "build"
    cleanup_build_dir(build_dir)

    # Phase 0: memory. Everything about what to make depends on what already
    # exists, so this runs before a single token is spent.
    topic, avoid_titles, episode = plan_run()
    if not topic:
        print("❌ Pipeline halted: every topic in the pool is already published.")
        print("   Add new entries to CORE_TOPICS in src/topics.py.")
        raise SystemExit(1)
    if episode:
        print(f"🔢 {SERIES_NAME} #{episode}")

    # Phase 1: script
    script_data = generate_video_script(
        topic=topic,
        avoid_titles=avoid_titles,
        title_max=min(80, title_budget(series_prefix(SERIES_NAME, episode))))
    if not script_data:
        print("❌ Pipeline halted: script generation failed.")
        raise SystemExit(1)

    # The question goes into the narration before any audio is rendered, so it
    # is spoken and captioned rather than only sitting in the comment field.
    first_comment = append_question_to_script(script_data)

    # Save the script for review/debugging (uploaded as a workflow artifact)
    with open(os.path.join(build_dir, "script.json"), "w", encoding="utf-8") as f:
        json.dump(script_data, f, indent=2, ensure_ascii=False)

    # Phase 2: assets
    script_data = generate_audio_pipeline(script_data, build_dir)
    script_data = generate_visual_pipeline(script_data, build_dir)

    usable = [s for s in script_data["scenes"]
              if s.get("image_path") and s.get("audio_path")]
    if len(usable) < 3:
        print("❌ Pipeline halted: fewer than 3 usable scenes.")
        raise SystemExit(1)

    # Phase 3: render
    final_video_path = generate_video_pipeline(script_data, build_dir)

    # Phase 4: metadata + upload
    title, description, tags = build_metadata(script_data, episode)
    print(f"📋 Title: {title}")

    if os.getenv("SKIP_UPLOAD") == "1":
        print("⏭️ SKIP_UPLOAD=1 — video rendered but not uploaded (dry run).")
    else:
        upload_video_to_youtube(final_video_path, title, description, tags,
                                first_comment=first_comment)

    print("🎉 Pipeline complete.")


if __name__ == "__main__":
    main()
