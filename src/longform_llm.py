"""
Long-form script engine — chaptered generation for the weekly deep dive.

Why this is separate from llm_engine.py: a free model asked for a 1,200-word
script as one JSON object reliably produces truncated JSON, collapsed structure,
or a 400-word script wearing a long script's schema. Generating one chapter per
call keeps every request inside the range these models are actually good at, and
a failure costs one chapter instead of the whole video.

The editorial arc is fixed in code rather than left to the model. The channel's
long format is an anatomy of a disaster: what was meant to happen, the decision,
the chain, the cost, and what would have broken the chain. Letting the model
choose its own structure every week is how a channel stops having a format.
"""

import json
import os
import re
import time

from openai import OpenAI

from audio_engine import is_speakable
from llm_engine import (get_free_models, normalize_thumbnail_fields,
                        validate_title, _extract_json, _validate_hook,
                        _has_proper_noun)
from llm_provider import get_pool, describe_provider, gemini_available
from visual_engine import (CANONICAL_STYLE, build_generation_prompt,
                           era_from_text)
from topics import pick_topic, format_of

import formats

# Words per chapter. edge-tts at +8% rate lands around 165 wpm, so ~1,200 words
# is roughly 7.5 minutes — inside the 6-9 minute target.
ARC = [
    # This chapter has been rewritten twice, and the history is worth keeping.
    #
    # First it said "state the anomaly in two sentences" while asking for 110
    # words. The model obeyed the brief, produced ~54 words, and failed the
    # length check twice — a self-inflicted deadlock. So the brief was given
    # material that actually fills the chapter: "open with the anomaly... then
    # widen: sketch the scale, name the people, and pose the question this video
    # answers."
    #
    # The widening is what killed it. Measured on the published videos, 69% of
    # viewers left inside the first 23 seconds — which is to say, inside this
    # chapter, while it was still setting up the question. Retention relative to
    # comparable videos sat at 0.12 through the opening: bottom decile. The fix
    # for a chapter that was too short was to give it more to say, and what it
    # found to say was preamble.
    #
    # So it no longer widens. It states the anomaly, makes it worse, and walks
    # straight into the story. 70 words is ~25 seconds; the old 90 spent 33
    # seconds before chapter two could begin.
    #
    # Renamed from "Cold Open" at the same time: the heading is typeset in the
    # description's chapter list, where it is production jargon addressed to
    # nobody.
    ("The Anomaly",
     "Open with the anomaly in one or two sharp sentences, the way a Short would: "
     "a hard number, a contradiction, or a named consequence, in the first "
     "sentence. Then make it WORSE with a second concrete fact — not broader, "
     "worse. Close by naming the person who made the decision and the moment they "
     "made it, so the next chapter can begin at their shoulder. "
     "Never announce what the video will cover, never pose the question the video "
     "answers, and never summarise the ending. No sentence may begin with 'In' "
     "followed by a year.",
     70),
    ("What Was Supposed To Happen",
     "Establish the plan as its designers understood it. The viewer should finish this "
     "chapter thinking the plan sounds sensible. Give the specifics: who, when, what "
     "resources, what the expected outcome was.",
     210),
    ("The Decision",
     "The turning point. Name who decided what, what information they had, what they "
     "lacked, and the pressure they were under. This is the heart of the video — be "
     "concrete and fair, not mocking.",
     260),
    ("The Chain",
     "How the decision became a disaster, in order. Use the timeline: what happened "
     "first, what each person did next, the moment it stopped being recoverable.",
     280),
    ("The Cost",
     "The consequences in hard numbers — dead, wounded, money, time, what was destroyed. "
     "Name at least one individual person if the record supports it. No sentimentality.",
     200),
    ("What Would Have Stopped It",
     "The counterfactual. What single change would have broken the chain, and what was "
     "actually changed afterwards as a result. End on the detail that lingers, not a moral.",
     180),
]

# THE UNSOLVED stories have no decision to sit at the middle of the arc and no
# counterfactual to close it, and the encore lane can absolutely send one here —
# the channel's most-liked Short is the Antikythera Mechanism, and it already
# became a long-form video that had to pretend a shipwreck was somebody's fault.
# Two chapters are replaced; the rest of the arc carries over unchanged, because
# "what was supposed to happen" and "what it cost" are true of a mystery too.
_UNSOLVED_SUBSTITUTIONS = {
    "The Decision": (
        "What Has Been Ruled Out",
        "The explanations that have been tested and failed, and why each one does not "
        "fit the evidence. Be specific about what the evidence actually is. This is "
        "where the channel earns its credibility, so never present a theory as settled "
        "and never invent a consensus that does not exist.",
        260),
    "What Would Have Stopped It": (
        "What Is Still Open",
        "The single detail that keeps this unresolved, and what would have to be found "
        "to settle it. State plainly what is known versus what is speculated. End on "
        "the open question — do NOT supply an answer the record does not support.",
        180),
}


def arc_for(episode_format: str):
    """The chapter arc for this episode format.

    Only THE UNSOLVED diverges. An ORDER and a DESIGN story are both a decision
    and its consequences; a mystery is an absence of one, and forcing it through
    the same six chapters is what the format split exists to stop.
    """
    if episode_format != "unsolved":
        return ARC
    return [_UNSOLVED_SUBSTITUTIONS.get(heading, (heading, brief, words))
            for heading, brief, words in ARC]

# Painterly, NOT photorealistic. This was briefly switched to a "35mm film
# still" treatment and the results got visibly worse: free image models render
# convincing paint, but photoreal skin and hands land in the uncanny valley,
# where every anatomical mistake reads as a mistake. In an illustration the
# same brushwork reads as style.
#
# Defined once in visual_engine and imported here so the instruction given to
# the model and the style actually sent to the generator can never drift apart.
STYLE_SUFFIX = CANONICAL_STYLE

# One image per ~32 spoken words — a cut roughly every 11 seconds. The old 45
# produced 13-frame nine-minute videos that reviewed as slideshows; the video
# engine additionally splits any scene that still runs past ~13 seconds.
WORDS_PER_IMAGE = 32

# ---------------------------------------------------------------------------
# Pacing profile
#
# Everything about the rhythm used to be one number applied to all six
# chapters: one cut rate, one narration speed, one music level. Seven minutes
# of a perfectly even pulse is the reason a retention graph slopes instead of
# stepping — the viewer's ear stops being surprised by anything around the
# ninety-second mark and the brain files the rest as "more of this".
#
# So the arc now breathes. Each chapter carries its own cut rate and its own
# narration speed, chosen against what that chapter is doing:
#
#   The Anomaly      fast cuts, fast read      — earn the next thirty seconds
#   Supposed To Happen  slow cuts, slower read — the calm before, and it must
#                                                actually feel calm
#   The Decision     tightest cuts, SLOWEST read — the midpoint reset. Slowing
#                                                down here is the interrupt:
#                                                after two chapters of pace,
#                                                a drop in speed reads as
#                                                "this is the part that matters"
#   The Chain        fastest cuts, fastest read — events overtaking people
#   The Cost         slow cuts, slow read       — the numbers need air
#   Counterfactual   medium both                — land it
#
# The index is positional, not keyed by heading: THE UNSOLVED substitutes two
# headings but keeps their slots, so a mystery's "What Has Been Ruled Out"
# inherits the pivot treatment its position earns.
# ---------------------------------------------------------------------------

# Which slot in the arc is the midpoint reset.
PIVOT_INDEX = 2

# words_per_image: lower = more cuts. tts_rate: edge-tts percentage.
PACING = [
    {"role": "open",  "words_per_image": 26, "tts_rate": "+10%"},
    {"role": "build", "words_per_image": 36, "tts_rate": "+6%"},
    {"role": "pivot", "words_per_image": 24, "tts_rate": "+2%"},
    {"role": "drive", "words_per_image": 22, "tts_rate": "+12%"},
    {"role": "cost",  "words_per_image": 34, "tts_rate": "+4%"},
    {"role": "close", "words_per_image": 30, "tts_rate": "+7%"},
]

# Used when a chapter index falls outside the table (a shorter arc, or an arc
# someone extends later without touching this file).
DEFAULT_PACING = {"role": "build", "words_per_image": WORDS_PER_IMAGE,
                  "tts_rate": os.getenv("TTS_RATE", "+8%")}


def pacing_for(index: int) -> dict:
    """The cut rate, narration speed and role for this slot in the arc."""
    if 0 <= index < len(PACING):
        return PACING[index]
    return DEFAULT_PACING


# Photography exists from roughly here. For stories older than this, "real
# imagery" means period artwork — paintings, engravings, portraits, maps —
# and every modern photograph is an anachronism. The first live run showed a
# 2020s aerial of a city over narration about 1415, which viewers rightly
# called out; the era gates below exist so that cannot recur.
PHOTOGRAPHY_YEAR = 1840


def _era_from_topic(topic: str):
    """Earliest plausible year named in the topic, or None. Good enough to
    decide which side of the photography line a story sits on."""
    return era_from_text(topic)

SYSTEM_PROMPT = f"""You write documentary narration for The Archive Paradox, a YouTube \
channel about catastrophic decisions: one order, one design flaw, one miscalculation, \
and the disaster it caused.

You write about REAL, verifiable events. Never invent facts, dates, numbers or quotes. \
If you are unsure of a specific figure, describe it qualitatively instead of guessing.

VOICE:
- Calm, factual, forensic. The events supply the drama; the narration does not add any.
- Short declarative sentences. Maximum 20 words each.
- Write for the ear: no parentheses, no semicolons, no bulleted lists.
- Concrete specifics over adjectives. "Forty tonnes of propellant" beats "an enormous amount".
- BANNED: "little did they know", "what happened next will shock you", "to this day", \
"lost to history", "the rest is history", "buckle up", "let that sink in".
- Do not address the audience. No "you might be wondering". No "we".

IMAGE PROMPTS:
- One clear focal subject each: one person, one object, one place. Crowded prompts \
produce muddy images.
- State the shot type first, and vary it: extreme close-up, wide establishing \
shot, low-angle, overhead, silhouette, over-the-shoulder.
- ONE FIGURE AT MOST, and commit to a distance. A single face filling the frame \
works. A lone silhouette works. An object or a landscape works. What fails is \
two or three half-size people around a table: the model has too little canvas \
per figure and renders them as smudges with broken hands. If a scene involves \
several people, choose one of them and frame that one.
- Never make hands the subject of the shot.
- ANCHOR THE ERA. Name the period's dress, ships, weapons, buildings and light \
sources explicitly ("15th-century carrack", "torchlit stone hall") so nothing \
modern can appear in a historical scene.
- Every prompt must end with exactly: "{STYLE_SUFFIX}"
"""


class QuotaExhausted(RuntimeError):
    """OpenRouter's free-model DAILY allowance is gone. Unlike a per-minute 429,
    no amount of waiting or model-switching helps until the quota resets."""


# OpenRouter's free tier allows ~50 requests a day across all free models, and a
# six-chapter script needs eight successful calls. The old fallback chain tried
# every model twice per call, wrapped in three outer attempts — up to 42 requests
# for one chapter, which exhausted the day's allowance in a single run.
#
# Two changes keep a whole video inside ~12 requests: each model is tried once,
# and the first model that works is remembered and tried first from then on, so
# later chapters do not re-walk the models that already failed.
MAX_MODELS_PER_CALL = 4
_preferred_model = None
_request_count = 0

# A model that returns unparseable output twice is not having a bad minute, it
# is wrong for this job — usually a reasoning model that spends its whole token
# budget thinking and emits prose where JSON was asked for. Retrying it costs
# requests from a 50/day budget and returns the same garbage, so it is dropped
# for the rest of the run.
MAX_PARSE_FAILURES = 2
_parse_failures = {}

# Models this key cannot use today: retired (404) or out of quota (429).
# Cleared only by starting a new run.
_retired = set()

# Providers whose daily allowance is gone. Tracked separately from _retired so
# the run can say which tiers it burned through, and so that hitting the wall
# on one provider retires that provider rather than ending the run.
_exhausted_providers = set()

FAILURE_LOG_DIR = os.path.join("build", "llm_failures")

# A 280-word chapter with six image prompts is around 1,200 tokens of JSON, and
# 2,000 left almost no headroom: reasoning models spend hundreds of tokens
# thinking before they emit anything, run into the ceiling mid-string, and the
# reply arrives truncated. Free models cost nothing per token, so the ceiling
# should sit well clear of the longest chapter rather than just above it.
CHAPTER_MAX_TOKENS = 4000

# Gemini's 3.x flash models think before they answer, and the thinking is
# charged against the same output ceiling as the reply. At 4,000 tokens every
# single chapter of a live run came back "Repaired a truncated JSON reply" and
# the video finished at 5.0 minutes instead of 7.5 — "The Decision", the
# chapter the whole format is built around, arrived with 56 words of its 260.
#
# Free-tier output limits on these models are far above this, so the ceiling
# was pure self-harm. Thinking is also switched off below, which returns the
# whole budget to the actual script.
GEMINI_CHAPTER_MAX_TOKENS = 16000

# "none" keeps flash from spending the reply budget on reasoning it does not
# need for a 280-word chapter. Set GEMINI_REASONING to low/medium to restore it.
GEMINI_REASONING = os.getenv("GEMINI_REASONING", "none")

CHAPTER_ATTEMPTS_GEMINI = 3
CHAPTER_ATTEMPTS_OPENROUTER = 2

# The arc has six chapters. Losing one late is survivable; losing three means
# the video no longer has a shape worth publishing.
MIN_PUBLISHABLE_CHAPTERS = 4


def _is_daily_quota(err: str) -> bool:
    return "free-models-per-day" in err or "free_tier_daily" in err


def _quota_reset(err: str) -> str:
    """OpenRouter reports the reset as epoch milliseconds buried in the error.
    Nobody should have to decode that by hand at 3am."""
    m = re.search(r"'X-RateLimit-Reset':\s*'(\d{10,13})'", err)
    if not m:
        return ""
    try:
        import datetime
        ts = int(m.group(1))
        when = datetime.datetime.fromtimestamp(ts / 1000, datetime.timezone.utc)
        delta = when - datetime.datetime.now(datetime.timezone.utc)
        hours = max(0, delta.total_seconds()) / 3600
        return (f" Resets {when:%Y-%m-%d %H:%M} UTC"
                f" (in about {hours:.1f}h).")
    except Exception:
        return ""


def _usable_models(models):
    """Drops models that have already proved they cannot return JSON today."""
    live = [m for m in models
            if _parse_failures.get(m, 0) < MAX_PARSE_FAILURES
            and m not in _retired]
    # If every model has been written off, try them all again rather than
    # returning nothing: a degraded reply still beats no reply.
    return live or list(models)


def _order_models(models):
    """Preferred model first, then the rest, capped so one call cannot walk the
    entire list."""
    models = _usable_models(models)
    if _preferred_model and _preferred_model in models:
        rest = [m for m in models if m != _preferred_model]
        ordered = [_preferred_model] + rest
    else:
        ordered = list(models)
    return ordered[:MAX_MODELS_PER_CALL]


def _reply_text(response):
    """The model's text, wherever it put it.

    Some reasoning models on OpenRouter return an empty `content` and leave the
    answer in `reasoning`. Treating that as "empty content" throws away a reply
    that often contains the JSON verbatim.
    """
    try:
        message = response.choices[0].message
    except (AttributeError, IndexError, TypeError):
        return ""
    for field in ("content", "reasoning", "reasoning_content"):
        text = getattr(message, field, None)
        if isinstance(text, str) and text.strip():
            return text
    return ""


def _log_raw_failure(model, content, error, finish_reason):
    """Keeps the raw reply so a failure can be diagnosed after the run.

    The workflow uploads build/ artifacts, so these land somewhere reachable
    instead of scrolling past in the Actions log, truncated to 160 characters.
    """
    try:
        os.makedirs(FAILURE_LOG_DIR, exist_ok=True)
        name = f"{model.replace('/', '_').replace(':', '_')}-{int(time.time())}.txt"
        with open(os.path.join(FAILURE_LOG_DIR, name), "w", encoding="utf-8") as f:
            f.write(f"model: {model}\nfinish_reason: {finish_reason}\n"
                    f"error: {error!r}\n\n=== RAW REPLY ===\n{content}\n")
    except Exception:
        pass                            # diagnostics must never break a run


def _max_tokens_for(provider: str, requested: int) -> int:
    """Gemini and Anthropic get real headroom; the free-tier providers keep the
    tight ceiling they need to stay inside a small daily allowance.

    Both of the roomy providers think before they answer, and the thinking is
    charged against the same ceiling as the reply — which is how a whole live
    run came back truncated at 4,000 tokens.
    """
    if provider in ("gemini", "anthropic"):
        return max(requested, GEMINI_CHAPTER_MAX_TOKENS)
    return requested


def _call(pool, system, user, max_tokens):
    """Runs a prompt down the model chain, one attempt per model.

    The chain spans providers: Gemini's models first, then OpenRouter's free
    ones, each with its own client. A run used to die outright when Gemini
    answered 404 and 429, despite a working OpenRouter key being configured.

    Raises QuotaExhausted the moment a daily allowance is gone, instead of
    walking the remaining models to collect identical 429s.
    """
    global _preferred_model, _request_count

    if not pool:
        raise RuntimeError("No LLM provider configured: set GEMINI_API_KEY "
                           "or OPENROUTER_API_KEY.")

    # A model that is out of quota is out for the run, not just this call.
    # Without this every chapter re-tried the same exhausted model first and
    # paid the same 429 before moving on.
    exhausted = set()

    for model in _order_models(pool.models):
        if model in exhausted:
            continue
        client = pool.client_for(model)
        provider = pool.provider_for(model)
        try:
            _request_count += 1
            kwargs = dict(
                model=model,
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": user}],
                temperature=0.7,
                max_tokens=_max_tokens_for(provider, max_tokens),
            )
            if provider == "gemini" and GEMINI_REASONING:
                kwargs["reasoning_effort"] = GEMINI_REASONING
            try:
                r = client.chat.completions.create(**kwargs)
            except TypeError:
                # An SDK or endpoint that does not know reasoning_effort must
                # not cost the run a chapter.
                kwargs.pop("reasoning_effort", None)
                r = client.chat.completions.create(**kwargs)
            finish_reason = getattr(r.choices[0], "finish_reason", "")
            content = _reply_text(r)
            if not content:
                raise ValueError("empty content")

            try:
                data = _extract_json(content)
            except Exception as parse_error:
                _log_raw_failure(model, content, parse_error, finish_reason)
                _parse_failures[model] = _parse_failures.get(model, 0) + 1
                # Never keep favouring a model that cannot produce JSON: the
                # preferred slot is what makes the next chapter try it first.
                if _preferred_model == model:
                    _preferred_model = None
                hint = (" (hit the token ceiling — reply was cut off)"
                        if finish_reason == "length" else "")
                raise ValueError(f"unparseable JSON{hint}: {parse_error}") from None

            if _preferred_model != model:
                print(f"   📌 using {model} for the rest of this run")
                _preferred_model = model
            return data
        except Exception as e:
            err = str(e)
            if _is_daily_quota(err):
                # A daily allowance is gone for THIS PROVIDER, not for the run.
                # This used to abort outright, which meant a spent OpenRouter
                # allowance threw away a working Gemini key sitting right
                # beside it — the exact single-point-of-failure the chain
                # exists to remove. Retire the provider and keep walking.
                _exhausted_providers.add(provider)
                for other in pool.models:
                    if pool.provider_for(other) == provider:
                        _retired.add(other)
                print(f"⛔ {provider} daily limit reached after "
                      f"{_request_count} request(s).{_quota_reset(err)}")
                remaining = _usable_models(pool.models)
                if not remaining:
                    raise QuotaExhausted(
                        f"Every configured provider is out of quota after "
                        f"{_request_count} request(s) this run "
                        f"({', '.join(sorted(_exhausted_providers))})."
                        f"{_quota_reset(err)}") from None
                print(f"   ↩️  Continuing on "
                      f"{', '.join(sorted({pool.provider_for(m) for m in remaining}))}.")
                continue
            # Truncate: these payloads are hundreds of lines of nested JSON.
            print(f"⚠️ {model} failed: {err[:160]}")
            if _parse_failures.get(model, 0) >= MAX_PARSE_FAILURES:
                print(f"   ⛔ dropping {model} for the rest of this run "
                      f"({MAX_PARSE_FAILURES} unparseable replies).")
            # A retired model never comes back mid-run, and a model out of its
            # daily quota will not recover either. Retiring both here is what
            # keeps six chapters from each paying the same two failures.
            if "404" in err or "no longer available" in err.lower():
                _retired.add(model)
                print(f"   ⛔ {model} is not available to this key — dropped.")
            elif "429" in err or "quota" in err.lower():
                _retired.add(model)
                print(f"   ⛔ {model} is out of quota — dropped for this run.")
                time.sleep(5)
    return None


def _archive_query_rule(era_year):
    """What archive_query should name depends on which side of the photography
    line the story sits. Pre-1840 stories were being told to return null, which
    routed every scene to AI generation or modern stock — the anachronism
    problem in one line."""
    # Shapes, not specimens. Naming real subjects here — this once offered
    # "HMS Hood 1941" — invites a model to reuse them in a story they have
    # nothing to do with, which is exactly how a casualty figure from the
    # key_stat example ended up published as another battle's death toll.
    if era_year and era_year < PHOTOGRAPHY_YEAR:
        return (
            "Each segment also gets archive_query: a 2-6 word phrase naming REAL "
            "PERIOD ARTWORK or artifacts held on Wikimedia Commons for that "
            "segment — a specific painting, engraving, tapestry, portrait, "
            "historic map, illuminated manuscript, or a surviving building or "
            "object, drawn from THIS story. Use the shapes "
            "\"<event name> painting\", \"<ruler's name> portrait\", "
            "\"<century> <vessel type> engraving\". This story predates "
            "photography, so NEVER name a photograph subject and NEVER a modern "
            "place name alone. Set it to null only when nothing depictable "
            "exists."
        )
    return (
        "Each segment also gets archive_query: a 1-6 word search phrase naming the "
        "REAL, PHOTOGRAPHABLE subject of that segment — the specific ship, person, "
        "aircraft, building or place FROM THIS STORY, as it would be captioned in "
        "an archive. Use the shapes \"<ship name> <year>\", "
        "\"<person's full name>\", \"<site name> <year>\". "
        "It is used to find a genuine historical photograph. Set it to null when "
        "the segment is abstract or hypothetical."
    )


# ---------------------------------------------------------------------------
# Open loops
#
# Every chapter used to be written to feel finished. The briefs said so
# outright — "end on the detail that lingers" — and each chapter was generated
# as an independent request that knew what had already been said and nothing
# about what came next. The result is six small essays played in sequence, and
# every seam between them is a place the viewer can leave without feeling they
# abandoned anything.
#
# The fix is not a teaser voice. This channel is calm and forensic, and
# "but that's not even the weird part" would cost more credibility than the
# retention is worth. What it does instead is end each chapter on a fact that
# is INCOMPLETE — an order already signed, a warning nobody had read, a number
# that does not add up yet. A flat declarative sentence the viewer cannot
# resolve on their own is an open loop, and it survives the channel's voice.
#
# The last chapter gets no handoff: there is nothing to hand off to, and a
# cliffhanger at the end is the thing that makes people distrust the next one.
# ---------------------------------------------------------------------------

def _handoff_rule(next_heading) -> str:
    """The instruction that turns a chapter ending into an open loop."""
    if not next_heading:
        return ("This is the FINAL chapter. Close the video. Do not open a new "
                "question, do not tease anything, and do not end on a moral. "
                "End on the concrete detail that stays with the viewer.\n\n")
    return (
        f"HAND OFF TO THE NEXT CHAPTER, which is titled \"{next_heading}\".\n"
        "Your LAST sentence must leave one thing unresolved, so the viewer needs "
        "the next chapter to settle it. State it as a flat fact, not a tease: "
        "name a document nobody had read, an order already signed, a person "
        "already on their way, or a number that does not yet add up. Keep it "
        "under 14 words.\n"
        "It must NOT summarise this chapter, and it must NOT be a question. "
        "BANNED in this sentence: \"but\" as the opening word, \"little did "
        "they know\", \"what happened next\", \"that's not even\", \"stay "
        "with me\", \"we'll get to that\", \"more on that\", \"as we will "
        "see\". Never address the viewer.\n\n")


def _chapter_prompt(topic, heading, brief, words, previous_tail, feedback="",
                    era_year=None, covered=None, episode_format=None,
                    next_heading=None, words_per_image=None):
    n_images = max(2, round(words / (words_per_image or WORDS_PER_IMAGE)))

    # The channel's promise for this episode type. Stated on every chapter
    # rather than once at the top, because each chapter is its own request
    # and the model has no memory of the others.
    format_line = (f"THIS EPISODE IS {formats.name_of(episode_format)}: "
                   f"{formats.brief_of(episode_format)}\n\n")

    # A 160-character tail was the whole of a chapter's memory, so every
    # chapter re-summarised the story from the top: one live video spent its
    # fourth chapter repeating "The port stayed open. The dead could not be
    # recovered." Handing over the actual sentences already spoken, and
    # forbidding them, is what stops that.
    continuity = ""
    if covered:
        recent = "\n".join(f"- {line}" for line in covered[-24:])
        continuity = (
            "\nTHESE POINTS HAVE ALREADY BEEN NARRATED EARLIER IN THIS VIDEO:\n"
            f"{recent}\n"
            "Do not restate any of them, do not paraphrase them, and do not "
            "summarise the story so far. This chapter must add new information "
            "only, moving the account forward.\n")
    elif previous_tail:
        continuity = (f"\nThe previous chapter ended with: \"{previous_tail}\"\n"
                      "Continue from there without repeating it.\n")
    # Retrying with a byte-identical prompt just reproduces the same rejected
    # output, which is exactly what happened on the first live trending run.
    correction = f"\n{feedback}\n" if feedback else ""
    era_note = ""
    if era_year:
        era_note = (f"\nTHE STORY TAKES PLACE AROUND {era_year}. Every image "
                    f"prompt must depict that period — the clothing, ships, "
                    f"weapons, buildings, tools and light sources of "
                    f"{era_year} — never anything modern.\n")
    return (
        f"Story: {topic}\n"
        f"{continuity}{correction}{era_note}\n"
        f"{format_line}"
        f"Write the chapter titled \"{heading}\".\n"
        f"Its job: {brief}\n\n"
        f"Write approximately {words} words of narration, and no fewer than "
        f"{int(words * 0.8)}. Split it into exactly "
        f"{n_images} consecutive segments; each segment gets its own image prompt. "
        f"Each segment should be roughly {max(20, words // n_images)} words — do not "
        f"write one-line segments.\n\n"
        f"{_archive_query_rule(era_year)}\n\n"
        "Also give this chapter's single most striking figure as key_stat — a bare "
        "number or short quantity and a four-word label, taken from THIS story. "
        "If the chapter has no meaningful figure, set key_stat to null rather "
        "than inventing one.\n\n"
        "Every sentence must carry information the viewer did not have a "
        "sentence ago. Never pad by restating an earlier line in new words.\n\n"
        f"{_handoff_rule(next_heading)}"
        # The placeholders below are deliberately not plausible facts. This
        # example once read {"value": "1,415", "label": "men lost with Hood"},
        # and a model copied 1,415 into a video about a different battle
        # entirely, as its casualty figure. An example that looks like an
        # answer will be used as one.
        "Output ONLY a JSON object, no markdown fences. Replace every "
        "placeholder below with material from this story:\n"
        '{"key_stat": {"value": "<figure from this story>", '
        '"label": "<four word label>"}, '
        '"segments": [{"narration": "<narration>", '
        '"image_prompt": "<shot type, subject, setting>", '
        '"archive_query": "<real subject to search for, or null>"}]}'
    )


# ---------------------------------------------------------------------------
# Trending lane
#
# The rule: the trend chooses the SUBJECT, history supplies the CONTENT. This
# pipeline publishes without a human reading the script first, and free models
# have a training cutoff, so they cannot know what happened yesterday and will
# confabulate if asked. Beyond accuracy, unreviewed commentary on a live war is
# a policy problem on YouTube and a neutrality claim nobody can verify.
#
# So a trending headline about the Strait of Hormuz becomes a documentary about
# the 1953 coup or the tanker war — never about this week. The cutoff year below
# is enforced on the model's chosen anchor AND on the narration it then writes,
# because prompting alone is not a control.
# ---------------------------------------------------------------------------

HISTORY_CUTOFF_YEAR = int(os.getenv("HISTORY_CUTOFF_YEAR", "2005"))

# Present-tense framing that would turn a history video into commentary.
PRESENT_TENSE_BANS = (
    "currently", "right now", "as of today", "this week", "in recent days",
    "ongoing war", "ongoing conflict", "at the time of writing", "nowadays",
)


def _recent_years(text: str, cutoff: int = None):
    """Years later than the cutoff mentioned anywhere in the narration."""
    cutoff = cutoff or HISTORY_CUTOFF_YEAR
    return sorted({int(y) for y in re.findall(r"\b(1\d{3}|20\d{2})\b", text)
                   if int(y) > cutoff})


def _violates_history_rule(text: str):
    """Returns a reason string if this narration strays into the present."""
    recent = _recent_years(text)
    if recent:
        return f"references {recent}, later than the {HISTORY_CUTOFF_YEAR} cutoff"
    low = text.lower()
    for phrase in PRESENT_TENSE_BANS:
        if phrase in low:
            return f"uses present-tense framing {phrase!r}"
    return None


def _entities(text: str):
    """Capitalised words and demonyms from a headline — the actors involved."""
    words = re.findall(r"\b[A-Z][a-zA-Z]{3,}\b", text or "")
    skip = {"The", "This", "That", "What", "When", "Where", "Why", "How",
            "After", "Before", "During", "With", "From", "Into", "Says",
            "Said", "New", "Two", "Three", "Amid", "Over", "Under"}
    return [w for w in words if w not in skip]


def _anchor_is_relevant(subject: str, topic: str, why: str) -> bool:
    """The anchor must explain the headline, not merely share a country with it.

    Checks that at least one actor named in the trending subject also appears in
    the chosen event or its stated connection. Prefix-matched so Ukraine and
    Ukrainian, or Spain and Spanish, count as the same actor.

    Without this the model happily answers a Ukraine headline with the history
    of the Trans-Siberian Railway: technically Russian, and useless to a viewer
    who came asking why the strikes are happening.
    """
    actors = _entities(subject)
    if not actors:
        return True          # nothing to check against; the year gate still applies
    haystack = f"{topic} {why}".lower()
    for a in actors:
        stem = a.lower()[:5]
        if stem and stem in haystack:
            return True
    return False


# The channel's format, in words a regex can look for. The trending lane is
# meant to be "hot subject, our kind of story" — the trend picks the SUBJECT,
# the niche decides WHICH story about it gets told. Without this the lane drifts
# into general explainer territory, which is how it published "Swiss Civil
# Protection Law 1963" and "1984 PKK Uprising": both perfectly real history,
# neither one a catastrophic decision, and both indistinguishable from any other
# history channel.
NICHE_MARKERS = (
    "decision", "decided", "decide", "order", "ordered", "chose", "choice",
    "refused", "refuse", "ignored", "ignore", "overruled", "rejected",
    "mistake", "error", "miscalculation", "misjudged", "misread", "blunder",
    "flaw", "failed", "failure", "collapse", "collapsed", "disaster",
    "catastrophe", "botched", "underestimated", "backfired", "doomed",
    "destroyed", "sank", "sunk", "killed", "defeat", "defeated", "lost",
    "abandoned", "delayed", "wrong", "gamble", "misplaced", "broke", "broken",
)


def _anchor_fits_niche(topic: str, why: str = "") -> bool:
    """True if the chosen event is a decision-and-consequence story.

    Deliberately checked against the topic sentence rather than the subject: the
    trending subject is a headline and is allowed to be anything. What has to
    fit the channel is the story we tell about it.
    """
    text = f"{topic} {why}".lower()
    return any(re.search(rf"\b{m}\b", text) for m in NICHE_MARKERS)


def select_historical_anchor(candidates, pool=None, avoid_titles=(),
                             attempts: int = 2):
    """Maps a trending subject onto a historical event old enough to be settled.

    Returns {"subject", "topic", "anchor_year", "why"} or None. None is not a
    failure — the caller falls back to the normal topic rotation, which is
    always preferable to publishing something the guardrail could not clear.

    The trend supplies the subject; the niche supplies the story. An anchor that
    is merely relevant history is rejected the same way one past the cutoff year
    is, and re-asked once with the reason, because dropping to the rotation
    throws away the only reason this lane exists.
    """
    if not candidates:
        return None

    pool = pool or get_pool()

    avoid_block = ""
    recent = [t for t in (avoid_titles or []) if t][:20]
    if recent:
        listed = "\n".join(f"- {t}" for t in recent)
        avoid_block = ("\nALREADY COVERED ON THIS CHANNEL — do not choose an "
                       f"event any of these is about:\n{listed}\n")

    listing = "\n".join(f"- {c['subject']}" for c in candidates[:10])
    prompt = (
        "Here are subjects currently in the news:\n\n" + listing + "\n\n"
        "Pick the ONE with the richest documentary history behind it, then name "
        "the historical event that explains THE SPECIFIC SITUATION IN THAT "
        "HEADLINE.\n\n"
        "RELEVANCE IS THE POINT. The viewer arrives asking why this is "
        "happening. The event you choose must be the origin of that exact "
        "dispute, border, alliance or grievance — not a famous episode that "
        "merely happens to involve the same country.\n"
        "- Headline about Russian strikes on Ukraine -> the 1994 Budapest "
        "Memorandum, or the 1954 transfer of Crimea. NOT the Trans-Siberian "
        "Railway, which involves Russia but explains nothing in the headline.\n"
        "- Headline about the Spanish-Moroccan border -> the 1415 capture of "
        "Ceuta and the contested sovereignty that followed. NOT the Spanish "
        "Armada.\n"
        "- Headline about the Strait of Hormuz -> the 1980s tanker war. NOT the "
        "construction of Persepolis.\n\n"
        "HARD RULES:\n"
        f"- The event must have CONCLUDED in {HISTORY_CUTOFF_YEAR} or earlier. "
        "Anything more recent is rejected.\n"
        "- Pick a settled, documented event, not a live dispute.\n"
        "- THE STORY MUST BE THIS CHANNEL'S KIND OF STORY: one decision, order, "
        "refusal or miscalculation, and the disaster that followed it. Not a "
        "law, not a treaty, not a general overview of a conflict, unless the "
        "point of the video is the choice someone made and what it cost.\n"
        "  GOOD: 'the 1918 decision to scuttle the Zeebrugge blockships in the "
        "wrong position, which left the canal open'.\n"
        "  BAD:  'the 1963 Swiss law requiring a bunker in every house' — real "
        "history, but nobody decided anything that went wrong.\n"
        f"{avoid_block}"
        "- Do not take a side, assign blame between present-day states, or "
        "describe current politics. You are choosing a history topic, not "
        "commenting on the news.\n"
        "- The topic must fit this channel: a decision, order or miscalculation "
        "and what it cost.\n\n"
        'Output ONLY JSON: {"subject": "the headline you chose", "topic": "one '
        'specific sentence describing the historical event", "anchor_year": '
        '1415, "why": "one sentence naming the countries or places involved and '
        'how this event produced the situation in the headline"}'
    )

    feedback = ""
    for attempt in range(1, max(1, attempts) + 1):
        # 3,000 tokens for a four-line JSON object looks absurd, but free
        # reasoning models spend their ceiling thinking before they emit
        # anything: at 600 this call truncated on three models in a row on a
        # live run, wasting three requests from the 50/day budget before a
        # single chapter was written.
        data = _call(pool,
                     "You choose historical documentary topics. Output only JSON.",
                     prompt + feedback, max_tokens=3000)
        if not data or not data.get("topic"):
            print("⚠️ No usable anchor returned.")
            return None

        try:
            year = int(data.get("anchor_year", 9999))
        except (TypeError, ValueError):
            year = 9999

        reason = None
        if year > HISTORY_CUTOFF_YEAR:
            reason = (f"the event you chose concluded in {year}, later than the "
                      f"{HISTORY_CUTOFF_YEAR} cutoff")
            print(f"⚠️ Anchor rejected: year {year} is later than the "
                  f"{HISTORY_CUTOFF_YEAR} cutoff. ({data.get('topic', '')[:70]})")
        elif not _anchor_is_relevant(data.get("subject", ""),
                                     data.get("topic", ""),
                                     data.get("why", "")):
            reason = ("the event you chose does not name anyone or anywhere "
                      "from the headline, so it explains nothing about it")
            print("⚠️ Anchor rejected: does not name any actor from the trending "
                  "subject, so it is background about the country rather than an "
                  "explanation of the story.")
        elif not _anchor_fits_niche(data.get("topic", ""), data.get("why", "")):
            reason = ("the event you chose is not a decision-and-consequence "
                      "story — name the choice someone made and the disaster it "
                      "caused, not a law, a treaty or a general overview")
            print(f"⚠️ Anchor rejected: not a catastrophic-decision story. "
                  f"({data.get('topic', '')[:70]})")

        if not reason:
            print(f"🧭 Trending subject: {data.get('subject', '?')}")
            print(f"   Anchored to {year}: {data['topic'][:100]}")
            return {"subject": data.get("subject", ""), "topic": data["topic"],
                    "anchor_year": year, "why": data.get("why", "")}

        if attempt >= attempts:
            break
        print("   Re-asking once with the rejection reason.")
        feedback = (f"\n\nYour previous answer was rejected because {reason}. "
                    "Choose a different subject from the list if that is what it "
                    "takes. Output ONLY the JSON object.")

    return None


def _chapter_usable(data, enforce_history=False):
    """Structural floor: real segments, and — for the trending lane — compliant
    with the history rule. A chapter failing this can never be published.

    Separate from the length check on purpose. Length is a quality preference
    and a short chapter is survivable; the history rule is a hard constraint and
    is enforced here so the best-effort fallback can never bypass it.
    """
    segs = (data or {}).get("segments")
    if not isinstance(segs, list) or not segs:
        return False
    for s in segs:
        if not s.get("narration") or not s.get("image_prompt"):
            return False
        # A truthy narration is not necessarily a sayable one: " " and "—"
        # both pass the check above and then break the TTS engines. This is
        # where such a segment should die, not twenty-seven minutes later.
        if not is_speakable(s["narration"]):
            print(f"⚠️ Chapter rejected: a segment has no speakable "
                  f"narration ({s['narration'][:30]!r}).")
            return False
    if enforce_history:
        reason = _violates_history_rule(" ".join(s["narration"] for s in segs))
        if reason:
            print(f"⚠️ Chapter rejected: {reason}.")
            return False
    return True


def _opening_sentence(data) -> str:
    """The first spoken sentence of a chapter — for the opening chapter, the
    first thing anyone hears."""
    for seg in (data or {}).get("segments") or []:
        text = (seg.get("narration") or "").strip()
        if text:
            return re.split(r"(?<=[.!?])\s+", text)[0].strip()
    return ""


def _opening_hook_ok(data) -> bool:
    """Judges the long-form opening by the Shorts' hook rules.

    The Shorts have had a hook validator since the channel's view decline was
    traced to weak first lines; the long-form lane had none, and it shows. Its
    opening chapter had 69% of viewers gone within 23 seconds and relative
    retention of 0.12 against comparable videos.

    Same standard, same reason: a viewer decides in the first sentence, and a
    seven-minute video does not get a longer grace period than a fifty-second
    one — it gets a shorter one, because it is asking for more.
    """
    sentence = _opening_sentence(data)
    if not sentence:
        return False
    return _validate_hook(sentence)


# Phrases that turn an open loop into a YouTube tease. The channel's whole
# credibility rests on sounding like it is reporting rather than selling, and a
# viewer who hears one of these stops believing the numbers too.
BANNED_HANDOFF_PHRASES = (
    "little did they know", "what happened next", "that's not even",
    "thats not even", "that is not even", "stay with me",
    "we'll get to that", "well get to that",
    "more on that", "as we will see", "as we shall see", "keep watching",
    "you won't believe", "you wont believe", "hold that thought",
)

# Openers that mean the sentence is closing the chapter rather than opening the
# next one.
_CONCLUSION_OPENERS = (
    "in the end", "ultimately", "in summary", "in total", "altogether",
    "overall", "in conclusion", "and that", "that was", "so the", "in all",
    "finally", "the result was", "and so",
)

# A sentence is an open loop when the viewer cannot close it themselves. These
# are the words that leave something dangling: an order already signed, a
# warning nobody had read, a person still three hours away.
_INCOMPLETE_MARKERS = (
    r"\balready\b", r"\bstill\b", r"\byet\b", r"\bnever\b", r"\bnobody\b",
    r"\bno one\b", r"\bnone\b", r"\bwould\b", r"\bhad not\b", r"\bhadn't\b",
    r"\bdid not\b", r"\bdidn't\b", r"\bwas not\b", r"\bwasn't\b",
    r"\bunread\b", r"\bunopened\b", r"\bunsigned\b", r"\bunaware\b",
    r"\bmissing\b", r"\bwrong\b", r"\bhad been\b", r"\bon its way\b",
    r"\bon his way\b", r"\bon her way\b", r"\bon their way\b",
)

# The prompt asks for under 14; a couple of words of slack beats spending a
# request from a 50-a-day budget on a sentence that is fine.
HANDOFF_MAX_WORDS = 16


def _closing_sentence(data) -> str:
    """The last spoken sentence of a chapter — the seam the viewer may leave at."""
    text = ""
    for seg in (data or {}).get("segments") or []:
        if (seg.get("narration") or "").strip():
            text = seg["narration"].strip()
    if not text:
        return ""
    parts = [p for p in re.split(r"(?<=[.!?])\s+", text) if p.strip()]
    return parts[-1].strip() if parts else ""


def _handoff_ok(data) -> bool:
    """Whether this chapter ends on an open loop rather than a full stop.

    Judged the same way the opening hook is judged, and for the same reason:
    the seam between two chapters is a decision point exactly like the first
    fifteen seconds, and the pipeline had a validator for one and nothing for
    the other. A chapter that ends complete is a chapter the viewer can close.
    """
    sentence = _closing_sentence(data)
    if not sentence:
        return False
    low = sentence.lower()

    if sentence.endswith("?"):
        print(f"⚠️ Handoff rejected: it asks the viewer a question. ({sentence!r})")
        return False

    words = len(sentence.split())
    if words > HANDOFF_MAX_WORDS:
        print(f"⚠️ Handoff rejected: {words} words, needs <= "
              f"{HANDOFF_MAX_WORDS}. ({sentence!r})")
        return False

    for phrase in BANNED_HANDOFF_PHRASES:
        if phrase in low:
            print(f"⚠️ Handoff rejected: tease phrase {phrase!r}. ({sentence!r})")
            return False

    # The prompt forbids opening this sentence with "but", because that is how
    # a factual handoff turns into a nudge.
    if low.startswith("but "):
        print(f"⚠️ Handoff rejected: it opens with 'but'. ({sentence!r})")
        return False

    for opener in _CONCLUSION_OPENERS:
        if low.startswith(opener):
            print(f"⚠️ Handoff rejected: it closes the chapter with "
                  f"{opener!r}. ({sentence!r})")
            return False

    # Something must actually be left hanging: a name, a figure, or a word that
    # marks the fact as incomplete.
    has_anchor = (_has_proper_noun(sentence)
                  or bool(re.search(r"\d", sentence))
                  or any(re.search(m, low) for m in _INCOMPLETE_MARKERS))
    if not has_anchor:
        print(f"⚠️ Handoff rejected: nothing is left open — no name, number "
              f"or unresolved fact. ({sentence!r})")
        return False

    return True


def _chapter_words(data):
    return sum(len(s["narration"].split()) for s in (data or {}).get("segments", []))


# ---------------------------------------------------------------------------
# Repetition control
#
# The failure this exists to catch, verbatim from a published run:
#
#   "The plan failed. The German fleet had the open canal. The port stayed
#    open. The dead could not be recovered. ... The plan failed. The German
#    fleet had the open canal. The port stayed open. The dead could not be
#    recovered."
#
# Seven minutes of that is not a documentary, and no amount of good imagery
# rescues it. Nothing in the pipeline noticed, because every individual check
# it faced — valid JSON, enough segments, enough words — passed.
# ---------------------------------------------------------------------------

# Below this share of distinct sentences, a chapter is padding.
MIN_UNIQUE_SENTENCE_RATIO = 0.72

# A chapter may echo a little of what came before; a third is far too much.
MAX_ECHO_RATIO = 0.25

# A finished script this repetitive is not worth publishing at all.
MIN_SCRIPT_UNIQUE_RATIO = 0.70


def _sentences(text: str):
    """Normalised sentences: lowercased, punctuation-stripped, trivial
    fragments dropped, so 'The plan failed.' and 'the plan failed' match."""
    out = []
    for raw in re.split(r"[.!?]+", text or ""):
        s = re.sub(r"[^\w\s]", "", raw.lower()).strip()
        s = re.sub(r"\s+", " ", s)
        if len(s.split()) >= 3:          # 'yes' or 'he did' are not padding
            out.append(s)
    return out


def _unique_ratio(sentences) -> float:
    return len(set(sentences)) / len(sentences) if sentences else 1.0


def _chapter_repetition(data, previous_sentences=()):
    """Returns a reason string if this chapter is padding, else None."""
    text = " ".join(s.get("narration", "")
                    for s in (data or {}).get("segments", []))
    sentences = _sentences(text)
    if len(sentences) < 4:
        return None                      # too short to judge fairly

    ratio = _unique_ratio(sentences)
    if ratio < MIN_UNIQUE_SENTENCE_RATIO:
        return (f"only {ratio:.0%} of its sentences are distinct — it is "
                f"repeating itself")

    if previous_sentences:
        seen = set(previous_sentences)
        echoed = sum(1 for s in sentences if s in seen)
        echo = echoed / len(sentences)
        if echo > MAX_ECHO_RATIO:
            return (f"{echo:.0%} of it repeats sentences from earlier "
                    f"chapters")
    return None


def _clean_archive_query(value):
    """Normalises a segment's archive_query, or returns None when the model
    sent nothing usable. A null here simply means the scene will be generated
    instead of sourced from the archives — never an error."""
    if not isinstance(value, str):
        return None
    q = " ".join(value.replace('"', " ").split())
    if not (3 <= len(q) <= 60) or len(q.split()) > 8:
        return None
    if q.lower() in {"null", "none", "n/a", "na", "unknown"}:
        return None
    return q


def _validate_chapter(data, expected_words, enforce_history=False,
                      previous_sentences=()):
    """Full check: usable, long enough, and not padding."""
    if not _chapter_usable(data, enforce_history):
        return False
    words = _chapter_words(data)
    # Generous band: the arc totals ~1,220 words, and a chapter landing at ~60%
    # is still usable. Below that the pacing collapses.
    if words < expected_words * 0.55:
        print(f"⚠️ Chapter too short: {words} words, wanted ~{expected_words}.")
        return False
    reason = _chapter_repetition(data, previous_sentences)
    if reason:
        print(f"⚠️ Chapter rejected: {reason}.")
        return False
    return True


# ---------------------------------------------------------------------------
# Promise and payoff
#
# The packaging call already writes the title against the finished script, so
# the thumbnail cannot promise something the video never covers. What nothing
# checked is the other direction: whether the video's own opening — the
# anomaly it leads with, the thing the viewer stayed for — is still the subject
# by the time it ends.
#
# It is a warning, never a rejection. Six chapters are written by this point,
# and a script that drifts is still a video; a script thrown away is not. But
# a run that trips this is worth seeing in the log, because it means the model
# lost the thread somewhere in the middle and the ending pays off a promise
# nobody made.
# ---------------------------------------------------------------------------

def payoff_ok(script: dict) -> bool:
    """Whether the closing chapter is still about what the opening promised.

    Compares the named actors in the first chapter against the last. No overlap
    means the video ended somewhere other than where it started.
    """
    chapters = (script or {}).get("chapters") or []
    if len(chapters) < 2:
        return True

    def named(chapter):
        text = " ".join(s.get("narration", "") for s in chapter.get("scenes", []))
        return set(_entities(text))

    opening, closing = named(chapters[0]), named(chapters[-1])
    # A chapter that names nobody cannot be judged this way — most often the
    # counterfactual, which is about a missing bolt rather than a person.
    if not opening or not closing:
        return True
    return bool(opening & closing)


def generate_longform_script(topic: str = None, enforce_history: bool = False,
                             era_year: int = None) -> dict:
    """Builds the full chaptered script. Returns None only if a chapter is
    unrecoverable — a single weak chapter is retried, not fatal to the run.

    enforce_history is set by the trending lane: it rejects any chapter that
    drifts past the cutoff year or slips into present-tense commentary.
    era_year anchors every visual to the story's period; when the caller does
    not know it (the rotation lanes), it is read out of the topic text.
    """
    print("🧠 Long-form LLM engine starting...")

    pool = get_pool()
    print(f"🤖 Writer: {describe_provider()}")
    print(f"   model chain: {', '.join(pool.models) or 'none'}")

    if topic is None:
        offset = int(os.getenv("LONGFORM_TOPIC_OFFSET", "-7"))
        topic = pick_topic(offset)
    if not topic:
        # pick_topic returns None once the pool is exhausted. Publishing nothing
        # beats publishing a repeat, which is the whole point of the exclusion.
        print("❌ No topic available — the pool is exhausted.")
        return None
    era_year = era_year or _era_from_topic(topic)
    print(f"📚 Deep dive topic: {topic}")
    if era_year:
        print(f"🕰️  Era anchor: ~{era_year} "
              f"({'pre' if era_year < PHOTOGRAPHY_YEAR else 'post'}-photography)")
    if enforce_history:
        print(f"🔒 History rule active: nothing after {HISTORY_CUTOFF_YEAR}.")

    episode_format = format_of(topic)
    arc = arc_for(episode_format)
    print(f"🏷️  Format: {formats.name_of(episode_format)} — "
          f"{formats.get(episode_format)['promise']}")

    opening_heading = arc[0][0]
    chapters, tail, short_chapters, dropped = [], "", 0, []
    # Everything already narrated, as normalised sentences and as plain lines.
    # The first feeds the repetition gate, the second is handed to the model so
    # it knows what not to say again.
    said_sentences, said_lines = [], []
    for c_index, (heading, brief, words) in enumerate(arc):
        pacing = pacing_for(c_index)
        # The last chapter closes the video; every other one hands off.
        next_heading = arc[c_index + 1][0] if c_index + 1 < len(arc) else None
        print(f"✍️  Chapter: {heading} (~{words} words, {pacing['role']}, "
              f"cut every ~{pacing['words_per_image']} words)...")
        data, best, feedback = None, None, ""
        # A weak handoff is worth exactly one extra request, not the whole
        # attempt budget: the seam matters, but not as much as having a
        # chapter at all, and the daily allowance is small.
        handoff_retried = False

        # Three attempts on Gemini, two on OpenRouter. The old flat two was
        # sized for a 50-request day; Gemini's free tier is far larger, and a
        # chapter arriving at 56 words of its 260 — as "The Decision" did — is
        # exactly the case worth spending one more request on.
        attempts = CHAPTER_ATTEMPTS_GEMINI if gemini_available() \
            else CHAPTER_ATTEMPTS_OPENROUTER
        for attempt in range(attempts):
            try:
                candidate = _call(pool, SYSTEM_PROMPT,
                                  _chapter_prompt(topic, heading, brief, words,
                                                  tail, feedback,
                                                  era_year=era_year,
                                                  covered=said_lines,
                                                  episode_format=episode_format,
                                                  next_heading=next_heading,
                                                  words_per_image=pacing["words_per_image"]),
                                  max_tokens=CHAPTER_MAX_TOKENS)
            except QuotaExhausted as e:
                print(f"🛑 {e}")
                # Nothing further can succeed today, so stop rather than burning
                # the remaining chapters against a wall.
                raise

            if _validate_chapter(candidate, words,
                                 enforce_history=enforce_history,
                                 previous_sentences=said_sentences):
                if heading == opening_heading and not _opening_hook_ok(candidate):
                    # Structurally fine, but it opens on setup. Keep it as the
                    # fallback — losing this chapter loses the video — and spend
                    # the remaining attempts trying for a real hook.
                    if best is None:
                        best = candidate
                    feedback = (
                        "Your previous attempt opened with setup. The FIRST sentence "
                        "of this chapter is the whole video's hook and is judged on "
                        "its own: at most 12 words, containing a hard number, a "
                        "proper name, or a superlative, and it may not begin with "
                        "'In', a year, 'It was', 'Imagine', 'This is', 'Picture "
                        "this', 'Have you ever', 'Most people' or 'What if'. "
                        "Write the anomaly itself, not an introduction to it.")
                    if attempt < attempts - 1:
                        print(f"   retrying chapter {heading!r} for a stronger "
                              f"opening ({attempt + 2}/{attempts})...")
                    continue

                # The chapter is sound. The remaining question is whether its
                # last sentence lets the viewer leave. Worth one retry, never
                # more: a closed ending is a real cost, an abandoned chapter is
                # a bigger one.
                if (next_heading and not handoff_retried
                        and attempt < attempts - 1
                        and not _handoff_ok(candidate)):
                    if best is None:
                        best = candidate
                    handoff_retried = True
                    feedback = (
                        "Your previous attempt ended the chapter completely, so "
                        "the viewer has no reason to continue. Rewrite it so the "
                        "LAST sentence leaves one thing unresolved that the "
                        f"chapter \"{next_heading}\" will settle — an order "
                        "already signed, a message nobody had read, someone "
                        "already on their way, a figure that does not yet add "
                        "up. Under 14 words, a flat statement of fact, not a "
                        "question and not a tease. Keep the rest of the chapter "
                        "as it was.")
                    print(f"   retrying chapter {heading!r} for an open ending "
                          f"({attempt + 2}/{attempts})...")
                    continue

                data = candidate
                break

            # Repetition needs its own correction. Telling a padding model to
            # "write more words" is how it padded in the first place.
            repetition = _chapter_repetition(candidate, said_sentences)
            if repetition and _chapter_usable(candidate, enforce_history):
                feedback = (
                    "Your previous attempt repeated itself: "
                    f"{repetition}. Write this chapter again with no repeated "
                    "or reworded sentences. Every sentence must add a fact the "
                    "viewer does not already have — a name, a time, a "
                    "measurement, or an action someone took. Do not summarise "
                    "what has already been said.")
                if attempt < attempts - 1:
                    print(f"   retrying chapter {heading!r} "
                          f"({attempt + 2}/{attempts})...")
                continue

            # Keep the longest structurally sound attempt as a fallback. The
            # history rule is checked here too, so it can never be bypassed.
            if _chapter_usable(candidate, enforce_history):
                if best is None or _chapter_words(candidate) > _chapter_words(best):
                    best = candidate
                got = _chapter_words(candidate)
                feedback = (f"Your previous attempt was only {got} words, which is "
                            f"too short. Write at least {int(words * 0.8)} words "
                            f"this time, with more specific detail — names, dates, "
                            f"figures, and what people actually did.")
            if attempt < attempts - 1:
                print(f"   retrying chapter {heading!r} "
                      f"({attempt + 2}/{attempts})...")

        if data is None and best is not None:
            # A weak chapter costs pacing. Abandoning the run costs the video.
            data = best
            got = _chapter_words(best)
            if got < words * 0.8:
                short_chapters += 1
            print(f"   ⚠️ Accepting the best attempt at {heading!r} ({got} "
                  f"words) rather than losing the run.")

        if not data:
            print(f"❌ Chapter {heading!r} could not be generated.")
            if heading == opening_heading:
                # The cold open is the only chapter the format cannot do
                # without: it is the hook, and everything after it is written
                # as a continuation of it.
                return None
            # Otherwise carry on. Five finished chapters used to be thrown away
            # because the sixth would not parse, which is a strange trade: the
            # viewer notices a missing counterfactual far less than they notice
            # no video at all.
            dropped.append(heading)
            print("   ↩️  Continuing without it.")
            continue

        segs = data["segments"]
        for s in segs:
            # Normalise rather than append. The old rule only added the style
            # when it was absent, so a model that wrote its own
            # ("photorealistic, 8k, cinematic photo") kept it and simply had
            # ours bolted on after — two styles in one prompt, and the
            # photoreal half won. build_generation_prompt strips competing
            # directions and applies the channel's style exactly once.
            s["image_prompt"] = build_generation_prompt(s["image_prompt"],
                                                        era_year=era_year)
            s["archive_query"] = _clean_archive_query(s.get("archive_query"))

        chapter_text = " ".join(x["narration"] for x in segs)
        said_sentences.extend(_sentences(chapter_text))
        said_lines.extend(
            line.strip() for line in re.split(r"(?<=[.!?])\s+", chapter_text)
            if len(line.split()) >= 4)

        stat = data.get("key_stat") or {}
        if isinstance(stat, dict) and stat.get("value"):
            # The on-screen callout is ~10 characters wide before it looks silly.
            stat = {"value": str(stat["value"])[:14],
                    "label": str(stat.get("label", ""))[:34]}
        else:
            stat = {}

        # arc_index, not the position in this list: a chapter that failed to
        # generate is dropped, and everything downstream that cares about the
        # midpoint would otherwise silently shift to the wrong chapter.
        chapters.append({"heading": heading, "scenes": segs, "key_stat": stat,
                         "arc_index": c_index, "pacing": pacing})
        tail = segs[-1]["narration"][-160:]
        total = sum(len(x["narration"].split()) for x in segs)
        print(f"   ✅ {len(segs)} segments, {total} words")

    if len(chapters) < MIN_PUBLISHABLE_CHAPTERS:
        print(f"❌ Only {len(chapters)} of {len(arc)} chapters survived — below "
              f"the {MIN_PUBLISHABLE_CHAPTERS} needed for a publishable arc.")
        return None
    if dropped:
        print(f"⚠️ Publishing without {len(dropped)} chapter(s): "
              f"{', '.join(dropped)}.")

    full_text = " ".join(s["narration"] for c in chapters for s in c["scenes"])

    # Last gate before anything is rendered. A video that repeats itself for
    # seven minutes earns no watch time and teaches the algorithm the channel
    # is not worth showing, so publishing it is worse for the channel than
    # publishing nothing. Better to burn the run and try again tomorrow.
    script_sentences = _sentences(full_text)
    script_ratio = _unique_ratio(script_sentences)
    if len(script_sentences) >= 12 and script_ratio < MIN_SCRIPT_UNIQUE_RATIO:
        print(f"❌ Halted: only {script_ratio:.0%} of the finished script's "
              f"sentences are distinct (need {MIN_SCRIPT_UNIQUE_RATIO:.0%}). "
              f"The models padded instead of writing.")
        print("   Publishing this would cost the channel more than skipping "
              "the run. Try again once the free models recover, or set "
              "GEMINI_API_KEY for a stronger writer.")
        return None
    print(f"🔎 Script variety: {script_ratio:.0%} distinct sentences.")
    if not payoff_ok({"chapters": chapters}):
        print("⚠️ The closing chapter names none of the people the opening "
              "did — the video may not pay off what it promised.")
    meta = {}
    try:
        meta = _packaging_call(pool, topic, full_text)
        # One correction pass, and only when the title is the problem. The
        # published long-form titles include "Soviet Invasion of Kuril Islands
        # 1945: The Hidden WW2 Battle" — a phrase that would fit any battle
        # ever fought, attached to a name almost nobody recognises. That
        # combination is the whole reason these videos are not clicked.
        if not validate_title((meta.get("title") or "").strip()):
            retry = _packaging_call(pool, topic, full_text, retitle=True)
            if retry.get("title") and validate_title(retry["title"].strip()):
                meta = retry
            else:
                print("   Keeping the first title — the retry was no better.")
    except QuotaExhausted:
        # Six chapters are already written. Losing the video over its title
        # would be absurd — fall back to topic-derived metadata.
        print("🛑 Quota gone before packaging; deriving metadata from the topic.")

    words = sum(len(s["narration"].split()) for c in chapters for s in c["scenes"])
    title = (meta.get("title") or topic[:70]).strip()

    script = {
        "topic": topic,
        "title": title,
        "description": (meta.get("description") or "").strip(),
        "tags": [t for t in (meta.get("tags") or []) if isinstance(t, str)],
        # Thumbnail text must be short or it turns to mush at 210px wide. Both
        # writers are given the same instruction and ignore it the same way, so
        # they share the repair: see llm_engine.normalize_thumbnail_fields.
        "thumbnail_text": meta.get("thumbnail_text"),
        "thumbnail_badge": meta.get("thumbnail_badge"),
        # Posted as the first comment under the video. See
        # youtube_publisher.post_first_comment for why the channel needs one.
        "comment_question": str(meta.get("comment_question") or "").strip()[:280],
        "chapters": chapters,
        "word_count": words,
        "era_year": era_year,
        "format": episode_format,
    }
    normalize_thumbnail_fields(script)

    note = f", {short_chapters} under target" if short_chapters else ""
    print(f"✅ Script complete: {len(chapters)} chapters, {words} words "
          f"(~{words / 165:.1f} min narrated{note})")
    print(f"📊 Used {_request_count} model request(s) this run.")
    if words < 700:
        print(f"⚠️ {words} words is well under the ~1,220 target — the video will "
              f"run short. Usually means the free models were degraded today.")
    return script


def _packaging_call(pool, topic, full_text, retitle=False):
    """Title, description, tags and thumbnail text, written against the finished
    script rather than guessed up front.

    retitle re-runs the call after the first title failed the shared rules,
    naming the failure so the second attempt is not the first one reworded.
    """
    correction = ""
    if retitle:
        correction = (
            "\nYOUR PREVIOUS TITLE WAS REJECTED. It either used a phrase that "
            "promises significance without naming anything — 'the hidden', 'the "
            "untold story', 'what really happened', 'the forgotten', 'the "
            "secret', 'the truth about', 'shocking' — or it leaned entirely on "
            "a proper noun the audience does not recognise. Write a different "
            "title, not a rewording: state the specific thing that went wrong "
            "and what it cost, in words someone outside the subject understands."
            "\n")
    return _call(pool,
                 "You write YouTube metadata. Output only JSON, no fences.",
                 f"This is the narration of a documentary about:\n{topic}\n\n"
                 f"{full_text[:3000]}\n\n"
                 f"{correction}"
                 "Write metadata for a YouTube SEARCH audience. The title must name the "
                 "event with the words someone would actually type, and state the "
                 "consequence. Under 70 characters. No clickbait the video does not "
                 "deliver, no ALL CAPS.\n"
                 "THE TITLE MUST BE UNDERSTANDABLE TO SOMEONE WHO HAS NEVER HEARD OF "
                 "THIS EVENT. Include the searchable name, but never let the name be "
                 "the only content — the name is what gets the video found, the stake "
                 "is what gets it clicked, and it needs both. 'Dogger Bank Incident "
                 "1904' names something nobody outside the subject recognises; "
                 "'Russia's Fleet Opened Fire on British Fishing Boats' says what "
                 "happened.\n"
                 "BANNED, because they would fit any story at all: 'the hidden ...', "
                 "'the untold story', 'what really happened', 'the forgotten ...', "
                 "'the secret ...', 'the truth about', 'shocking', 'insane'.\n"
                 "Also write thumbnail_text: THREE OR FOUR WORDS ONLY, the most "
                 "arresting fact in the video, readable at thumbnail size. Not a "
                 "sentence, no punctuation. Good: 'SUNK IN 20 MINUTES', 'THE WRONG "
                 "VALLEY', 'ONE MISSING BOLT'. Bad: 'The story of how a ship sank'.\n"
                 "And thumbnail_badge: a year or a single number, e.g. '1628'.\n"
                 "And comment_question: ONE question to post as the first comment, "
                 "answerable by someone who just watched this and has an opinion — "
                 "who was at fault, which moment was the point of no return, what "
                 "they would have done. Name the people or the choice. Never "
                 "'what do you think?' on its own.\n"
                 'Output: {"title": "...", "description": "3 sentences, no hashtags", '
                 '"tags": ["10-14 search tags including proper nouns"], '
                 '"thumbnail_text": "...", "thumbnail_badge": "...", '
                 '"comment_question": "..."}',
                 # Same headroom logic as the anchor call: reasoning models
                 # think inside max_tokens, and a truncated reply costs a
                 # request from the daily budget and returns nothing.
                 max_tokens=2500) or {}


# Silence deliberately left at a chapter seam. Not dead air — dead air is what
# the trimmer removes. This is the opposite: a measured beat where the previous
# sentence is allowed to land before the next chapter starts talking.
CHAPTER_BEAT = 0.35

# The breath before the midpoint. Longer, because it is doing work: after two
# chapters of narration at pace, most of a second of nothing is itself the
# pattern interrupt that says the video just changed gear.
PIVOT_BEAT = 0.9

# The last line of the video gets room before the end card fades up.
CLOSING_BEAT = 0.6


def flatten_scenes(script: dict) -> list:
    """Flattens chapters into the scene list the audio/visual engines expect.
    Chapter titles are typeset over the footage by the video engine (keyed off
    chapter_index changes), so every scene gets real imagery — the old
    full-screen chapter cards are gone.

    Each scene also carries the pacing its chapter earned: the narration speed
    the audio engine reads it at, the role the video engine grades and scores
    it by, and the beat of silence that follows it.
    """
    chapters = script["chapters"]
    # Which position in the final list is the midpoint, given that chapters can
    # be dropped. Falls back to the arithmetic middle when no chapter declares
    # itself the pivot, so a short arc still gets a reset somewhere sensible.
    pivot_pos = next((i for i, c in enumerate(chapters)
                      if (c.get("pacing") or {}).get("role") == "pivot"),
                     len(chapters) // 2 if len(chapters) >= 4 else None)

    scenes = []
    for c_idx, chapter in enumerate(chapters):
        pacing = chapter.get("pacing") or DEFAULT_PACING
        last = len(chapter["scenes"]) - 1
        for s_idx, scene in enumerate(chapter["scenes"]):
            s = dict(scene)
            s["chapter_index"] = c_idx
            s["chapter_heading"] = chapter["heading"]
            s["chapter_role"] = pacing.get("role", "build")
            s["is_pivot"] = (c_idx == pivot_pos)
            s["tts_rate"] = pacing.get("tts_rate")
            # The visual engine gates archival search and b-roll on the era.
            s["era_year"] = script.get("era_year")
            if s_idx == 0:
                # longform_video reads key_stat off the chapter's first scene.
                s["key_stat"] = chapter.get("key_stat") or {}
            if s_idx == last:
                if c_idx == len(chapters) - 1:
                    s["pause_after"] = CLOSING_BEAT
                elif pivot_pos is not None and c_idx == pivot_pos - 1:
                    s["pause_after"] = PIVOT_BEAT
                else:
                    s["pause_after"] = CHAPTER_BEAT
            scenes.append(s)
    return scenes


if __name__ == "__main__":
    s = generate_longform_script()
    if s:
        print(json.dumps(s, indent=2)[:2000])
