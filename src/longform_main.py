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
from llm_provider import get_pool
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
        block, budget = ["Archival imagery and footage:"], 1800
        for line in lines:
            if len(line) + 1 > budget:
                block.append("…and others.")
                break
            block.append(f"• {line}")
            budget -= len(line) + 1
        parts.append("\n".join(block))
    # Only the scenes still missing a specific credit came from Pexels —
    # real archival video (see longform_visual.py) already has its own
    # per-clip credit line in `credit`, same as archival photos.
    if any(s.get("video_path") and not s.get("credit") for s in scenes):
        parts.append("Stock footage courtesy of Pexels.")
    return "\n\n".join(parts)


GENERATED_SHARE_TO_GATE = 0.3


def check_visual_quality(workers_ai_generated: int, total_generated: int,
                         total_shots: int = 0) -> None:
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
    if total_shots and total_generated / total_shots < GENERATED_SHARE_TO_GATE:
        # A video that is mostly real archive: a handful of fallback frames
        # does not make it "visibly worse". The fourth Six-Day War run was
        # halted over 20 generated frames in a video of real photos and
        # footage, after an hour of work.
        print(f"🖼️  {total_generated} generated frame(s) of {total_shots} shots "
              f"({workers_ai_generated} from Workers AI) — mostly real archive, "
              f"no quality halt.")
        return

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


def build_sources_block(script, budget):
    """"Sources:" for the description: the Wikipedia article the story was
    checked against, then each work cited on screen, within `budget`
    characters ("" when there is nothing to list or no room)."""
    article = script.get("source_article") or {}
    works = script.get("claim_sources") or []
    if not article.get("url") and not works:
        return ""
    lines = ["Sources:"]
    if article.get("url"):
        lines.append(f"• Wikipedia, \"{article.get('title') or ''}\": {article['url']}")
    for w in works:
        line = f"• {w['full']}"
        if len("\n".join(lines + [line, "…and others."])) > budget:
            lines.append("…and others.")
            break
        lines.append(line)
    block = "\n".join(lines)
    return block if len(block) <= budget and len(lines) > 1 else ""


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

    # Sources of the claims (story mode): the article, then every cited work
    # a scene showed. Placed before the image credits and sized to what is
    # left, so it can never push the required music credit past YouTube's
    # 5,000-character limit.
    sources = build_sources_block(script, 4900 - len("\n\n".join(parts)) - 2)
    if sources:
        at = next((i for i, p in enumerate(parts)
                   if p.startswith("Archival imagery")), len(parts))
        parts.insert(at, sources)

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


def build_story(topic, build_dir, given=False):
    """Research + fact sheet + outline (story_mode.py), printed for review.
    Returns {"sheet", "plan", "era_year"} or None."""
    from collections import Counter
    from llm_provider import get_pool as _pool
    from longform_llm import _call
    import story_mode as sm
    pool = _pool()
    article = None
    for query in sm.article_queries(pool, topic, _call):
        article = sm.research(query)
        if article:
            break
    if not article:
        print(f"⚠️ Story mode: no Wikipedia article found for {topic!r}.")
        return None
    print(f"📚 Research: {article['title']} ({len(article['text']):,} chars)")
    # A weak model's sheet (the fourth run's had no figures and no quotes)
    # gets one more try on another model; the richer sheet is kept.
    import longform_llm as _llm
    sheet, tried_sheet = None, set()
    for attempt in range(2):
        def scall(p, system, user, max_tokens):
            return _call(p, system, user, max_tokens=max_tokens, skip=tried_sheet)
        got = sm.fact_sheet(pool, topic, article, scall)
        if _llm._last_model:
            tried_sheet.add(_llm._last_model)
        size = lambda sh: (len(sh["timeline"]) + 2 * len(sh["quotes"]) + len(sh["numbers"])) if sh else -1
        if size(got) > size(sheet):
            sheet = got
        if sheet and sheet["quotes"] and sheet["numbers"]:
            break
        if attempt == 0:
            print("   ↩️  Thin fact sheet — trying once more on another model.")
    if not sheet:
        print("⚠️ Story mode: the fact sheet came back too thin to tell a story.")
        return None
    if not sheet["numbers"] and not sheet["quotes"] and not given:
        # An idea, not an event: the 2026-09-30 weekly run was handed
        # "Blackstone's ratio" (a legal principle) by the encore lane, found
        # no figure and no quote in it, and failed a chapter later.
        print("⚠️ Story mode: no figure and no quote in the article — this "
              "topic is an idea, not an event with a story.")
        return None
    # Three tries, each on a different model: one weak model's malformed
    # outline must not cost the whole story (it did on the fourth run).
    tried, plan = set(), None
    for attempt in range(3):
        def call(p, system, user, max_tokens):
            return _call(p, system, user, max_tokens=max_tokens, skip=tried)
        plan = sm.outline(pool, topic, sheet, call)
        if _llm._last_model:
            tried.add(_llm._last_model)
        if plan:
            break
        print(f"   ↩️  Outline attempt {attempt + 1}/3 unusable "
              f"({_llm._last_model or 'no model'}) — trying another model.")
    if not plan:
        print("⚠️ Story mode: the outline did not come back usable.")
        return None
    dropped = sm.verify_beats(plan, article["text"])
    for d in dropped:
        print(f"   ↩️  Beat dropped, not in the source: {d[:110]}")
    if len(plan["chapters"]) < 5:
        print("⚠️ Story mode: too few chapters survived verification.")
        return None
    sm.fix_roles(plan["chapters"])
    sm.print_plan(sheet, plan)
    sm.save(build_dir, sheet=sheet, plan=plan)
    years = Counter(int(y) for t in sheet["timeline"]
                    for y in re.findall(r"\b(1\d{3}|20\d{2})\b", t.get("date", "")))
    return {"sheet": sheet, "plan": plan, "sources": article.get("sources"),
            "era_year": years.most_common(1)[0][0] if years else None}


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
        # One pool for the whole lane: channel-watch's theme extraction and
        # select_historical_anchor are both LLM calls, and building the
        # pool twice would repeat provider-availability checks (and any
        # network calls they make, like discovering Gemini's model list)
        # for no benefit — _preferred_model is a separate global that
        # already persists across calls regardless.
        pool = get_pool()
        avoid_titles = recent_titles(history, 20)

        # Checked first, ahead of the trend feeds below: a live political
        # talk show can have a heated, highly-searched debate up hours
        # before Trends or News RSS catch up to it. Off by default
        # (WATCH_CHANNEL_HANDLE unset) and, even enabled, only ever hands
        # select_historical_anchor a generic theme label — never a video's
        # own title or description. See channel_watch.py's docstring for
        # why that boundary is load-bearing, not stylistic.
        try:
            from channel_watch import get_watch_candidates
            watch_candidates = get_watch_candidates(pool=pool,
                                                     avoid_titles=avoid_titles)
            if watch_candidates:
                # three tries: the watched channel is the point of this lane,
                # and a news headline is only the fallback
                anchor = select_historical_anchor(
                    watch_candidates, pool=pool, avoid_titles=avoid_titles,
                    ranked=True, story=True, attempts=3)
                if anchor:
                    print(f"📺 Anchored to a channel-watch theme: "
                          f"{anchor['topic'][:70]}")
                    return anchor["topic"], True, False, anchor.get("anchor_year")
        except QuotaExhausted:
            raise
        except Exception as e:
            print(f"⚠️ Channel watch failed ({type(e).__name__}: {e}).")

        try:
            from trend_source import get_trending_subjects
            region, candidates = get_trending_subjects()
            print("↩️  No channel-watch theme could be anchored — using the news "
                  "headlines instead.")
            anchor = select_historical_anchor(
                candidates, pool=pool, avoid_titles=avoid_titles, story=True)
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
        story_topic = os.getenv("STORY_TOPIC", "").strip()
        stage = os.getenv("STORY_STAGE", "full").strip().lower()
        # A run that died (spent quota, a chapter nobody could write) left its
        # topic, story and finished chapters behind: carry on from there
        # instead of paying for them again (run_cache.py).
        import run_cache
        saved = run_cache.load()
        resumed = bool(saved) and (not story_topic or saved["topic"] == story_topic)
        if resumed:
            topic, story = saved["topic"], saved["story"]
            enforce_history = saved.get("enforce_history", False)
            from_short = saved.get("from_short", False)
            era_year = saved.get("era_year") or story.get("era_year")
            print(f"♻️  Resuming the last run's story: {topic[:90]} "
                  f"({len(saved.get('chapters') or {})} chapter(s) already written).")
            run_cache.mark_resumed()
        else:
            if story_topic:
                # A named story (e.g. the Six-Day War remake): no channel watch,
                # no anchor — the topic is given, story mode tells it.
                topic, enforce_history, from_short, era_year = story_topic, True, False, None
                print(f"📖 Story topic given: {topic} (stage: {stage})")
            else:
                topic, enforce_history, from_short, era_year = resolve_topic(lane, history)
            if not topic:
                print("❌ Halted: every topic in the pool is already published. "
                      "Add new entries to CORE_TOPICS in src/topics.py.")
                raise SystemExit(1)
            if os.getenv("TOPIC_ONLY", "0") == "1":
                # Stops after the selection calls: tests channel watch and anchor
                # choice against a real model and the real channel for two or
                # three LLM requests, instead of a full render and upload.
                print(f"🔎 TOPIC_ONLY — selected topic: {topic}")
                print(f"   enforce_history={enforce_history} era_year={era_year}")
                return
            # Story mode is the only long-form format (owner, 2026-09-28): every
            # lane, every topic. The old six-chapter arc is never a fallback — a
            # run whose story cannot be built stops instead.
            from longform_llm import _anchor_fits_niche, idea_to_event

            def as_event(t):
                """t when it reads as an event; else the real event that best
                tells the idea (Maimonides' principle -> the Salem witch
                trials), or None. Checked before any research is spent: the
                2026-09-30 weekly run spent two runs' quota on that idea."""
                if _anchor_fits_niche(t, story=True):
                    return t
                ev = idea_to_event(get_pool(), t, recent_titles(history, 20) if history else ())
                if ev:
                    print(f"💡 Idea turned into its event: {t[:60]!r} -> {ev[:90]}")
                else:
                    print(f"↩️  {t[:70]!r} reads as an idea and no event tells it — skipped.")
                return ev

            tried = {topic}
            if story_topic:
                story = build_story(topic, BUILD_DIR, given=True)
            else:
                event = as_event(topic)
                if event:
                    topic = event
                    tried.add(topic)
                    story = build_story(topic, BUILD_DIR)
                else:
                    story = None
            while not story and not story_topic and len(tried) < 6:
                # An automatic lane can pick another topic: its first pick may
                # be an idea rather than an event (see build_story). A named
                # topic never changes.
                covered = covered_topics(history, longform_only=True) if history else set()
                nxt = pick_topic(int(os.getenv("LONGFORM_TOPIC_OFFSET", "-7")) - len(tried),
                                 exclude=covered | tried)
                if not nxt or nxt in tried:
                    break
                tried.add(nxt)
                event = as_event(nxt)
                if not event:
                    continue
                topic, enforce_history, from_short = event, False, False
                tried.add(topic)
                print(f"↩️  Trying another topic: {topic[:80]}")
                story = build_story(topic, BUILD_DIR)
            if not story:
                print("❌ Story mode could not build a fact sheet and outline — "
                      "stopping rather than writing it in the old arc.")
                raise SystemExit(1)
            era_year = era_year or story.get("era_year")
            run_cache.save_story(topic, story, enforce_history, era_year, from_short)
        if stage == "outline":
            print("🛑 STORY_STAGE=outline — stopping for approval of the plan.")
            return
        script = generate_longform_script(topic=topic,
                                          enforce_history=enforce_history,
                                          era_year=era_year, story=story)
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
    if os.getenv("STORY_STAGE", "full").strip().lower() == "script":
        with open(os.path.join(BUILD_DIR, "longform_script.json"), "w",
                  encoding="utf-8") as f:
            json.dump(script, f, indent=2, ensure_ascii=False)
        print("=" * 70)
        print(f"STORY MODE — SCRIPT: {script.get('title')}")
        for c in script["chapters"]:
            print(f"\n## {c['heading']}  [{(c.get('pacing') or {}).get('role')}]")
            for sc in c["scenes"]:
                print(sc["narration"])
        print("=" * 70)
        print("🛑 STORY_STAGE=script — stopping for approval of the script.")
        return
    script["lane"] = lane
    script["has_short"] = from_short

    with open(os.path.join(BUILD_DIR, "longform_script.json"), "w",
              encoding="utf-8") as f:
        json.dump(script, f, indent=2, ensure_ascii=False)

    scenes = flatten_scenes(script)
    if story and story.get("sources"):
        # per-claim sources (docs/VIDEO_SPEC.md): each line that clearly
        # matches a cited sentence of the article carries that footnote's
        # source on screen; all of them are listed in the description
        from claim_sources import attach
        script["claim_sources"] = attach(scenes, story["sources"])
        script["source_article"] = {"title": story["sources"].get("title"),
                                    "url": story["sources"].get("url")}
        print(f"📎 {sum(1 for s in scenes if s.get('claim_source'))} of "
              f"{len(scenes)} scenes carry a source; "
              f"{len(script['claim_sources'])} distinct works cited.")
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
        scenes, BUILD_DIR, topic=script.get("topic"))

    usable = [s for s in scenes if s.get("image_path") and s.get("audio_path")]
    if len(usable) < 8:
        print(f"❌ Halted: only {len(usable)} usable scenes.")
        raise SystemExit(1)

    # Before spending the render, not after: a video nobody should publish is
    # not worth an hour of Actions time either.
    check_visual_quality(workers_ai_generated, total_generated,
                         total_shots=sum(len(s.get("image_paths") or [1]) for s in usable))

    # Phase 3: render
    video_path, total, chapter_marks, music_attribution = generate_longform_video(
        scenes, BUILD_DIR, intro_epigraph=script.get("intro_epigraph"),
        verdict_epigraph=script.get("verdict_epigraph"))

    minutes = total / 60
    if minutes < 4:
        print(f"⚠️ Video is only {minutes:.1f} min — shorter than the 6-9 min target.")
    elif minutes > 12:
        print(f"⚠️ Video is {minutes:.1f} min — longer than the 6-9 min target.")

    # Phase 4: thumbnail. Story mode makes up to three (thumb_variants.py),
    # uploads the first and keeps all of them in build/thumbnails/ for
    # YouTube Studio's Test & Compare; the single frame below is the fallback.
    thumb_path = None
    try:
        from thumb_variants import build as build_thumbnails
        variants = build_thumbnails(scenes, script, BUILD_DIR)
        if variants:
            thumb_path = variants[0][1]
            print(f"🖼️  {len(variants)} thumbnail(s) for Test & Compare: "
                  f"{', '.join(k for k, _ in variants)}; uploading {variants[0][0]}.")
    except Exception as e:
        print(f"⚠️ Thumbnail variants failed ({type(e).__name__}: {e}) — "
              "using the single-frame thumbnail.")
    try:
        source = None if thumb_path else pick_source_scene(scenes)
        if source:
            thumb_path = os.path.join(BUILD_DIR, "thumbnail.jpg")
            # the year alone: the old format names ("THE ORDER") mean
            # nothing in a story video
            badge = (script.get("thumbnail_badge") or "").strip()
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

    video_id = None
    if os.getenv("SKIP_UPLOAD") == "1":
        print("⏭️ SKIP_UPLOAD=1 — rendered but not uploaded (dry run).")
        print("--- description ---")
        print(description)
    else:
        video_id = upload_video_to_youtube(video_path, title, description, tags,
                                           thumbnail_path=thumb_path,
                                           first_comment=first_comment,
                                           caption_path=caption_path,
                                           playlist_title=SERIES_NAME,
                                           playlist_description=PLAYLIST_DESCRIPTION)

    if video_id:
        run_cache.clear()
    if video_id:
        # for the thumbnail rotation (tools/thumb_rotation.py): the workflow
        # registers this video's thumbnails under its id
        with open(os.path.join(BUILD_DIR, "uploaded.json"), "w", encoding="utf-8") as f:
            json.dump({"video_id": video_id}, f)

    # Phase 6: Shorts cut from this video, each linking back to it. After the
    # upload, because the link is the point; rendered on dry runs too, so
    # they can be watched in the artifact first.
    publish_clips(scenes, script, video_id)

    print("🎉 Long-form pipeline complete.")


def publish_clips(scenes, script, video_id):
    """Cuts and uploads the long-form Shorts (longform_clips.py). Nothing
    here may fail the run: the long-form video is already up."""
    from longform_clips import LONGFORM_CLIPS, clip_metadata, make_clips
    if LONGFORM_CLIPS <= 0:
        return
    try:
        clips = make_clips(scenes, script, BUILD_DIR)
    except Exception as e:
        print(f"⚠️ Clips skipped ({type(e).__name__}: {e}).")
        return
    for clip in clips:
        clip_title, clip_description, comment = clip_metadata(
            clip, script, video_id, credit_block=build_credit_block(clip["scenes"]))
        print(f"📋 Clip: {clip_title}")
        if os.getenv("SKIP_UPLOAD") == "1":
            print("--- clip description ---")
            print(clip_description)
            continue
        if not video_id:
            print("⚠️ Clip not uploaded: the long-form upload returned no id "
                  "to link to.")
            continue
        try:
            upload_video_to_youtube(clip["path"], clip_title, clip_description,
                                    ["history", "shorts", "documentary"],
                                    first_comment=comment)
        except Exception as e:
            print(f"⚠️ Clip upload failed ({type(e).__name__}: {e}).")


if __name__ == "__main__":
    main()
