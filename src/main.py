import json
import os
import re
import shutil

import formats

from topics import format_of as topics_format_of
from caption_track import SRT_NAME
from llm_engine import generate_video_script
from thumbnail_engine import make_thumbnail, pick_source_scene
from topics import pick_topic
from published_history import (FORCE, GUARD_HOURS, covered_topics, describe,
                               load_published, next_episode_number,
                               promised_topic, published_within, recent_titles,
                               series_suffix)
from topic_reach import is_saturated, vet
from audio_engine import generate_audio_pipeline
from visual_engine import generate_visual_pipeline
from video_engine import generate_video_pipeline
from youtube_publisher import upload_video_to_youtube


def cleanup_build_dir(build_dir="build"):
    if os.path.exists(build_dir):
        shutil.rmtree(build_dir)
    os.makedirs(build_dir)


SUFFIX = " #shorts"


def title_budget(series: str = "") -> int:
    """Characters left for the story title itself. YouTube's ceiling is 100 and
    both the series marker and the #shorts tag are spent out of it."""
    return 100 - len(SUFFIX) - len(series)


def sanitize_title(title: str, series: str = "") -> str:
    """YouTube: max 100 chars, no angle brackets. Cut on a word boundary.

    Story first, then the series marker, then the tag — because the front of
    the title is the only part the Shorts feed shows, and it belongs to the
    words that tell a viewer which video this is.
    """
    clean = re.sub(r"[<>]", "", title or "").strip()
    max_body = title_budget(series)
    if len(clean) > max_body:
        clean = clean[:max_body].rsplit(" ", 1)[0].rstrip(".,;:")
    return clean + series + SUFFIX


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

# Every upload is filed here. A playlist is the only surface that hands a viewer
# the next episode instead of returning them to the feed, and the channel has
# never had one — 9,000 views have produced a single playlist view.
PLAYLIST_DESCRIPTION = (
    f"{SERIES_NAME} — one catastrophic decision, explained. "
    "Every episode is a real event, a real choice, and what it cost."
)

# Broad tags are close to worthless on their own, but they cost nothing and the
# specific ones from the script are what give the video a chance in search.
#
# Format-NEUTRAL only. "military history" and "engineering failures" used to sit
# here and went onto every upload, so a DESIGN story about an airliner was
# tagged military and an ORDER story about a cavalry charge was tagged
# engineering. formats.tags_of supplies those now, correctly, per episode.
BASE_TAGS = ["history", "shorts", "history shorts", "historical disasters",
             "documentary"]


def _strip_hashtags(text: str) -> str:
    """Removes the writer's own hashtags from the description body.

    YouTube counts the first three hashtags it finds ANYWHERE and renders them
    above the title, so a writer that sprinkles its own decides the video's
    visible category by accident. The channel picks them instead, once, at the
    bottom — see formats.hashtags_of.
    """
    cleaned = re.sub(r"(?:^|\s)#[\w]+", " ", text or "")
    return re.sub(r"[ \t]{2,}", " ", cleaned).strip()


def build_metadata(script_data: dict, episode=None):
    series = series_suffix(SERIES_NAME, episode)
    title = sanitize_title(script_data.get("title")
                           or script_data["scenes"][0]["narration"], series)

    description = _strip_hashtags(script_data.get("description", ""))

    # Restating the subject in plain words gives the description something
    # indexable — right now 96% of views come from the Shorts feed alone.
    topic = (script_data.get("topic") or "").strip()
    if topic:
        description += f"\n\n{topic}"

    series_line = ""
    if SERIES_NAME:
        numbered = f" No. {episode}" if episode else ""
        fmt = formats.get(script_data.get("format"))
        series_line = (f"\n\n{SERIES_NAME}{numbered} · {fmt['name']} — "
                       f"{fmt['promise']}.")
    description += series_line

    # The next episode's full pool line, under the exact "Next file: " prefix
    # promised_topic() parses. This line IS the pipeline's cross-run memory:
    # tomorrow's run reads it back and publishes this story, so the tease the
    # narrator just spoke stays a promise instead of a guess.
    next_topic = (script_data.get("next_topic") or "").strip()
    if next_topic:
        description += f"\n\nNext file: {next_topic}"

    # Exactly three: #shorts earns the format placement, and the two most
    # valuable from the episode format fill the rest. A fourth would never
    # render, so it is not written.
    tags_line = " ".join(
        ["#shorts"] + formats.hashtags_of(script_data.get("format"))[:2])
    description += (
        f"\n\n{CHANNEL_NAME} — one catastrophic decision, explained, {UPLOAD_CADENCE}."
        f"\nSubscribe if you want the next one."
        "\n\nNote: visuals in this video are AI-generated illustrations of real"
        f" historical events.\n{tags_line}"
    )

    tags = script_data.get("tags") or []
    tags = [t.strip() for t in tags if isinstance(t, str) and t.strip()][:15]
    tags += formats.tags_of(script_data.get("format"))
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


# Whether the comment question is ALSO spoken at the end of the video.
#
# OFF by default now. Six episodes shipped with a spoken closing question and
# the pattern is settled: the model writes a false binary about a dead man's
# motives ("Was this a calculated risk or pure arrogance?"), it costs ~7 of the
# video's ~60 seconds, and across those episodes it produced zero comments. It
# also displaced the script's designed last line — the prompt asks for "one
# final line that lingers" and then the pipeline talked over it. The question
# still gets posted as the first comment, where it costs nothing; the spoken
# seconds at the end belong to the next-file tease, which at least earns them.
SPEAK_QUESTION = os.getenv("SPEAK_QUESTION", "0") == "1"

# Whether the outro tease — "Next file: <next story>. The archive stays open."
# — is spoken and captioned over the video's last seconds. This is the
# channel's signature and its subscribe reason in one: a named next story is a
# cliffhanger, "a story for another day" is filler. The story it names is the
# one tomorrow's run actually publishes (see promised_topic).
SPEAK_TEASE = os.getenv("SPEAK_TEASE", "1") == "1"


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


def _era_of(topic: str):
    """The year a pool topic is set in, for dating the next-file card's frame.
    Reuses the visual engine's reader so both sides agree on what counts."""
    from visual_engine import era_from_text
    return era_from_text(topic)


def next_file_subject(topic: str) -> str:
    """The next story's name, for the card's headline. The pool line's head —
    "The Radcliffe Line (1947), drawn in five weeks..." -> "The Radcliffe Line".
    """
    head = re.split(r"[(,;:]", topic or "", maxsplit=1)[0].strip()
    return " ".join(head.split()[:6])


def add_outro_scene(script_data: dict) -> None:
    """Appends the next-file tease as its own SCENE, in place.

    It used to be extra words bolted onto the last scene's narration, which is
    how it shipped in episode 7 — and watching that back, nothing told you the
    video had stopped being about Cartagena and started advertising the next
    one. Same voice, same picture, same slow zoom, one more sentence. A trailer
    needs a cut, so the outro is now a scene: its own image, its own card, and
    a hard change of subject at the exact moment the narrator changes subject.

    Being a real scene also means it gets the rest of the pipeline for free —
    edge-tts word timings, word-synced captions, the crossfade — instead of
    each of those needing a special case.
    """
    tease = (script_data.get("next_tease") or "").strip().rstrip(".!?…")
    next_topic = (script_data.get("next_topic") or "").strip()
    scenes = script_data.get("scenes") or []
    if not (SPEAK_TEASE and tease and next_topic and scenes):
        return
    if any(s.get("is_outro") for s in scenes):
        return

    subject = next_file_subject(next_topic)
    # Banning people outright made this the flattest frame in the video — an
    # empty room next to five frames with a person in them. One DISTANT figure
    # brings it back without risking the thing the ban was for, which was a
    # half-melted face sitting under the headline. Same rule the writer already
    # follows for the story frames: one figure at most, commit to a distance.
    #
    # era_year is the NEXT story's period, not this one's. Without it the frame
    # inherits the era of the story just told and illustrates the wrong decade
    # — see generate_visual_pipeline.
    scenes.append({
        "narration": f"Next file: {tease}. The archive stays open.",
        "image_prompt": (
            f"wide atmospheric establishing shot evoking {subject}, "
            "at most one distant figure seen small in the frame or as a "
            "silhouette, no close-up faces, "
            "dramatic chiaroscuro lighting, dark moody cinematic oil painting, "
            "visible expressive brushwork, rich texture, muted color palette "
            "with one accent color, highly detailed, no text, no watermark"),
        "era_year": _era_of(next_topic),
        "is_outro": True,
        "outro_subject": subject,
        "outro_tease": tease,
    })
    print(f"🔗 Outro scene added — NEXT FILE: {subject} / {tease}")


def draw_outro_card(script_data: dict, build_dir: str) -> None:
    """Replaces the outro scene's plain image with the next-file card.

    Never raises: the card is worth having and not worth losing a finished
    video over. Without it the scene still plays, just as a picture.
    """
    scene = next((s for s in script_data.get("scenes") or []
                  if s.get("is_outro")), None)
    if not scene or not scene.get("image_path"):
        return
    try:
        from thumbnail_engine import make_next_file_card
        next_format = topics_format_of(scene.get("next_format_topic")
                                       or script_data.get("next_topic") or "")
        year = re.search(r"\b(1[0-9]{3}|20[0-9]{2})\b",
                         script_data.get("next_topic") or "")
        badge = f"{formats.name_of(next_format)}"
        if year:
            badge += f" · {year.group(0)}"
        from video_engine import CTA_TEXT
        make_next_file_card(
            scene["image_path"], scene["outro_subject"], scene["outro_tease"],
            scene["image_path"], accent=formats.accent_of(next_format),
            badge=badge, cta=CTA_TEXT)
        print(f"🪪 Next-file card drawn [{badge}]")
    except Exception as e:
        print(f"⚠️ Next-file card failed ({type(e).__name__}: {e}) — "
              "the outro plays as a plain image.")


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


# Whether to draw and upload a custom thumbnail for Shorts.
#
# OFF, on evidence. The pipeline drew one, thumbnails().set returned success,
# the log said "Custom thumbnail set for OX4z93EMmAA" — and the Short still
# shows a frame from the video. A 200 from that endpoint means the bytes were
# accepted and stored, not that anything renders them: Shorts surfaces take
# their poster from the video itself, and the Data API has no way to change it.
#
# So this spent 50 quota units and a render step per upload to produce a log
# line that was not true. The drawing code is untouched and still used by the
# long-form pipeline, where custom thumbnails do work and are most of what
# earns the click in search. Set SHORTS_THUMBNAIL=1 if YouTube changes this.
SHORTS_THUMBNAIL = os.getenv("SHORTS_THUMBNAIL", "0") == "1"


def build_thumbnail(script_data: dict, build_dir: str):
    """Draws the 16:9 thumbnail for a Short, or returns None.

    Never raises: a thumbnail is worth having and not worth losing a finished
    video over.
    """
    if not SHORTS_THUMBNAIL:
        print("🖼️  Shorts thumbnail skipped — YouTube renders a video frame "
              "for Shorts regardless. Set SHORTS_THUMBNAIL=1 to override.")
        return None
    try:
        source = pick_source_scene(script_data.get("scenes") or [])
        if not source:
            print("⚠️ No usable frame for a thumbnail — YouTube will pick one.")
            return None
        headline = script_data.get("thumbnail_text") or script_data.get("title", "")
        if not headline.strip():
            return None
        path = os.path.join(build_dir, "thumbnail.jpg")
        episode_format = script_data.get("format")
        # The badge carries the format before the year, so the three episode
        # types are legible as a set even before the colour registers.
        year = (script_data.get("thumbnail_badge") or "").strip()
        badge = f"{formats.name_of(episode_format)} · {year}" if year \
            else formats.name_of(episode_format)
        make_thumbnail(source, headline, path, badge=badge,
                       accent=formats.accent_of(episode_format))
        print(f"🖼️  Thumbnail: {headline!r} [{badge}] "
              f"({os.path.getsize(path) / 1024:.0f} KB)")
        return path
    except Exception as e:
        print(f"⚠️ Thumbnail generation failed ({type(e).__name__}: {e}) — "
              "YouTube will auto-select a frame.")
        return None


# The look of this channel is FLUX-1-schnell on Workers AI. Pollinations serves
# the same prompt from a shared free endpoint and the result is visibly worse —
# which is fine as a rescue for one frame and not fine as the whole video.
#
# On 19 August the day's third manual run found Cloudflare's 10,000-neuron free
# allocation already spent by the two scheduled runs, quietly rendered all five
# frames on the fallback, and published. Nothing in the log said the video was
# below standard, because nothing was checking.
#
# The floor is a share of frames, not a count, so it holds however many scenes a
# script has. Set MIN_PREFERRED_FRAMES=0 to publish anyway.
MIN_PREFERRED_FRAMES = float(os.getenv("MIN_PREFERRED_FRAMES", "0.6"))


def check_visual_quality(script_data: dict) -> None:
    """Halts before upload when the frames did not come from the good generator.

    Only applies when Workers AI is actually configured. With no Cloudflare
    credentials Pollinations IS the intended generator, and refusing to publish
    would be refusing to publish at all.
    """
    from visual_engine import cloudflare_available
    if not cloudflare_available() or MIN_PREFERRED_FRAMES <= 0:
        return

    scenes = script_data.get("scenes") or []
    if not scenes:
        return
    preferred = sum(1 for s in scenes if s.get("image_source") == "workers-ai")
    share = preferred / len(scenes)
    if share >= MIN_PREFERRED_FRAMES:
        return

    print(f"\n❌ Halted: only {preferred}/{len(scenes)} frames came from Workers "
          f"AI (floor is {MIN_PREFERRED_FRAMES:.0%}).")
    print("   Cloudflare's free allocation is 10,000 neurons a day and resets at "
          "00:00 UTC. Three runs of five frames is roughly all of it, so a manual "
          "run stacked on the day's scheduled one will land here.")
    print("   Publishing this would put a visibly worse video on the channel "
          "under the same title and thumbnail as a good one.")
    print("   Wait for the reset, or set MIN_PREFERRED_FRAMES=0 to publish anyway.")
    raise SystemExit(1)


def plan_run():
    """Reads the channel back before writing anything.

    Returns (topic, avoid_titles, episode, next_topic). Raises SystemExit(0) — a clean stop,
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
        topic = pick_topic()
        upcoming = pick_topic(exclude=[topic]) if topic else None
        return topic, [], None, upcoming

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

    # The last upload's outro promised a specific next story, on screen and out
    # loud. Keep it. Saturation used to override the promise here, and that was
    # the wrong place to check: episode 8 teased the Iranian hostage crisis,
    # the next run measured it at 9 big Shorts, dropped it, and published the
    # Enabling Act instead — so the card had advertised a video that was never
    # going to exist. The check belongs BEFORE the promise is made, and it is
    # done there now, so anything reaching this point was already vetted.
    #
    # A topic that has since been published is still refused: that is
    # correctness, not preference, and republishing it would be worse than
    # breaking the tease.
    topic = None
    promised = promised_topic(history)
    if promised:
        if promised in used:
            print(f"⚠️ The promised next file has since been published — "
                  f"falling back to the rotation. ({promised[:60]})")
        else:
            print(f"🔗 Keeping the last upload's promise: {promised[:70]}")
            topic = promised

    if topic is None:
        # The static reach ratings are a prior; this checks the live catalogue.
        # A topic the feed is already full of is skipped for the next candidate.
        topic, rejected = vet(lambda excl: pick_topic(exclude=excl), used)
        for r in rejected:
            print(f"   ↩︎ skipped as saturated: {r[:70]}")

    # Tomorrow's story, teased in this video's outro and written into its
    # description as the promise tomorrow's run reads back. Vetted HERE, which
    # is the whole point: the tease is a public commitment, so nothing goes on
    # the card that the next run would then refuse to make. A smaller budget
    # than today's topic — it shares the same Data API allowance, and one
    # obviously-saturated candidate skipped is most of the value.
    upcoming = None
    if topic:
        upcoming, passed_over = vet(lambda excl: pick_topic(exclude=excl),
                                    set(used) | {topic}, max_checks=2)
        for r in passed_over:
            print(f"   ↩︎ not promised, saturated: {r[:70]}")

    episode = next_episode_number(history, SERIES_NAME)
    return topic, recent_titles(history), episode, upcoming


def main():
    print(f"🚀 Initiating the {CHANNEL_NAME} pipeline...")
    build_dir = "build"
    cleanup_build_dir(build_dir)

    # Phase 0: memory. Everything about what to make depends on what already
    # exists, so this runs before a single token is spent.
    topic, avoid_titles, episode, upcoming = plan_run()
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
        title_max=min(80, title_budget(series_suffix(SERIES_NAME, episode))),
        series_name=SERIES_NAME,
        next_topic=upcoming or "")
    if not script_data:
        print("❌ Pipeline halted: script generation failed.")
        raise SystemExit(1)
    if upcoming:
        script_data["next_topic"] = upcoming

    # Outro additions go into the narration before any audio is rendered, so
    # they are spoken and captioned. Question first (off by default), then the
    # tease — the signature line closes the video.
    first_comment = append_question_to_script(script_data)
    add_outro_scene(script_data)

    # Save the script for review/debugging (uploaded as a workflow artifact)
    with open(os.path.join(build_dir, "script.json"), "w", encoding="utf-8") as f:
        json.dump(script_data, f, indent=2, ensure_ascii=False)

    # Phase 2: assets
    script_data = generate_audio_pipeline(script_data, build_dir)
    script_data = generate_visual_pipeline(script_data, build_dir)
    draw_outro_card(script_data, build_dir)

    usable = [s for s in script_data["scenes"]
              if s.get("image_path") and s.get("audio_path")]
    if len(usable) < 3:
        print("❌ Pipeline halted: fewer than 3 usable scenes.")
        raise SystemExit(1)

    # Before spending the render, not after: a video nobody should publish is
    # not worth eight minutes of Actions time either.
    check_visual_quality(script_data)

    # Phase 3: render
    final_video_path = generate_video_pipeline(script_data, build_dir)

    # Phase 4: thumbnail. Shorts autoplay in the feed, which is why this was
    # skipped — but the feed is not the only place a Short appears. On the
    # channel page, in search results and in suggested, it is a 16:9 thumbnail
    # like any other video, and all 42 published so far have been an arbitrary
    # frame YouTube picked.
    thumb_path = build_thumbnail(script_data, build_dir)

    # Phase 5: metadata + upload
    title, description, tags = build_metadata(script_data, episode)
    print(f"📋 Title: {title}")

    caption_path = os.path.join(build_dir, SRT_NAME)
    if not os.path.exists(caption_path):
        caption_path = None

    if os.getenv("SKIP_UPLOAD") == "1":
        print("⏭️ SKIP_UPLOAD=1 — video rendered but not uploaded (dry run).")
    else:
        upload_video_to_youtube(final_video_path, title, description, tags,
                                thumbnail_path=thumb_path,
                                first_comment=first_comment,
                                caption_path=caption_path,
                                playlist_title=SERIES_NAME,
                                playlist_description=PLAYLIST_DESCRIPTION)

    print("🎉 Pipeline complete.")


if __name__ == "__main__":
    main()
