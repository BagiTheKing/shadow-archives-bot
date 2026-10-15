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

import datetime
import json
import os
import re
import time

from openai import OpenAI

import run_cache
from audio_engine import is_speakable
from llm_engine import (get_free_models, normalize_thumbnail_fields,
                        validate_title, _extract_json, _validate_hook,
                        _has_proper_noun, CLICKBAIT_MODE)
from llm_provider import get_pool, describe_provider, gemini_available
from visual_engine import (CANONICAL_STYLE, build_generation_prompt,
                           era_from_text)
from topics import pick_topic, format_of
from quote_search import search_quotes

import formats

# Words per chapter. edge-tts at +8% rate lands around 165 wpm, so ~1,200 words
# is roughly 7.5 minutes — inside the 6-9 minute target.
ARC = [
    # This opening chapter has now been rewritten three times, and the earlier
    # history (below, kept because the reasoning still holds) solved the wrong
    # problem. The old fix made the anomaly-first opening SHORTER, which
    # improved a 23-second cliff — but 67 videos and two months later, the
    # channel's numbers (27 subscribers, 10k views, 1.67% overall thumbnail
    # CTR) said the anomaly itself was never the draw. A viewer has no reason
    # to want a mechanism explained until they know what it cost.
    #
    # So the video no longer opens on the anomaly — it opens on the human cost,
    # the material that used to sit in what was chapter 5. What the old opener
    # did (state the anomaly, name the decision-maker) now happens in chapter
    # 2, "The Claim", once the viewer already has a reason to care who that
    # person is.
    #
    # [History] First it said "state the anomaly in two sentences" while
    # asking for 110 words. The model obeyed the brief, produced ~54 words, and
    # failed the length check twice — a self-inflicted deadlock. So the brief
    # was given material that actually fills the chapter: "open with the
    # anomaly... then widen: sketch the scale, name the people, and pose the
    # question this video answers." The widening is what killed IT: 69% of
    # viewers left inside the first 23 seconds, while the chapter was still
    # setting up the question. So it stopped widening — anomaly, worse, walk
    # into the story. That fixed the 23-second cliff without fixing the
    # subscriber number, which is the problem this version addresses instead.
    ("The Cost",
     "Open cold with the single darkest, most catastrophic or most mysterious "
     "true fact of this whole story — not just any fact, the one detail that "
     "makes a total stranger's stomach drop. Lead with it in the first "
     "sentence, framed for maximum intrigue. If a number sharpens that fact, "
     "use exactly one — the sharpest one available — paired with one concrete "
     "human or physical detail in the same breath. Do not stack a second "
     "number on top of it: a pile of statistics reads as a data dump, not a "
     "gut-punch, and this chapter only gets one punch to throw. No "
     "throat-clearing, no context, no date-first framing. Hit it, then stop. "
     "This chapter is judged the way a Short's hook is judged: a hard number, "
     "name or named consequence by the second sentence, because its only job "
     "is to make someone stop scrolling.",
     90),
    # The chapter the title is written from. It states, as a live possibility
    # rather than a settled fact, the harshest reasonable reading of what
    # chapter 1 just described. Packaging later lifts directly from this
    # chapter to write the title, so what gets written here has to be an
    # actual claim a viewer could repeat back, not a mood.
    ("The Claim",
     "This is the chapter the title gets written from — it has to shine "
     "harder than any other chapter in the video. Given what the viewer just "
     "learned was lost, state the harshest reasonable interpretation of why "
     "— framed as what the facts suggest, not a verdict you have already "
     "reached. Build it like a case forming in real time, not a conclusion "
     "announced flatly: name the specific, real details that make that "
     "reading plausible one at a time — a warning that existed, a person who "
     "objected, a choice that looks deliberate rather than accidental — so "
     "the suspicion visibly hardens sentence by sentence. Do not answer it "
     "yet — that is the rest of the video's job. End this chapter still "
     "holding the question open, on the single most damning detail you "
     "have.",
     170),
    ("What Was Supposed To Happen",
     "Ground the viewer in the real scene before explaining the plan "
     "mechanically — the place, the time of day, what it looked and sounded "
     "like, who was actually there — then establish the plan as its "
     "designers understood it. The viewer should finish this chapter "
     "thinking the plan sounds sensible. Give the specifics: who, when, what "
     "resources, what the expected outcome was. This is a reflective "
     "chapter, not a fast one: sentences here may run longer than the "
     "voice's usual cap when the extra length is doing real work — painting "
     "the scene, not padding it — up to about 30 words instead of 20.",
     210),
    ("The Decision",
     "The turning point. Name who decided what, what information they had, what they "
     "lacked, and the pressure they were under. This is the heart of the video — be "
     "concrete and fair, not mocking. This is the pivot the whole arc slows down "
     "for: sentences here may run longer than the voice's usual cap when the extra "
     "length lets the viewer sit inside the pressure of the moment — up to about "
     "30 words instead of 20.",
     260),
    ("The Chain",
     "How the decision became a disaster, in order. Use the timeline: what happened "
     "first, what each person did next, the moment it stopped being recoverable. "
     "Narrate it with an actual running clock wherever the record supports one — "
     "name specific times or elapsed minutes (\"At 3:14, the first alarm sounds. "
     "Six minutes later...\") so the viewer can feel it ticking, not just read a "
     "sequence. Only use real times the record actually supports; never invent a "
     "timestamp for pacing.",
     280),
    # The verdict on chapter 2's claim, using only what chapters 3-5 actually
    # established. This is also the counterfactual's home now — naming the
    # single change that would have broken the chain is one legitimate shape
    # for the closing lesson, not a chapter of its own anymore.
    ("The Verdict",
     "Answer the question chapter 2 raised, using only what the video has already "
     "shown — confirm it, complicate it, or overturn it, whatever the evidence "
     "actually supports. Only after that verdict is delivered, zoom out: name what "
     "this specific catastrophe reveals about people in general — how anyone can "
     "miss a warning, trust the wrong assumption, or pay for someone else's "
     "certainty — whatever is true here, whether the cause was a human decision, "
     "a natural force, or both. Earn this turn, do not assert it cold; it only "
     "lands because of everything the video already showed. If real people were "
     "lost or hurt, do not let the zoom-out erase them — name what was actually "
     "lost one last time before the final line, the same way the video opened. "
     "Then close on either a stated lesson or a genuinely open question the "
     "evidence itself leaves unresolved — not a tease, an ending, but the kind of "
     "ending that makes someone want to know how this channel would tell the "
     "next one. If you name a counterfactual (the single change that would have "
     "broken the chain), that can BE the lesson.",
     210),
]

# THE UNSOLVED stories have no decision-maker to accuse and no counterfactual
# to close on, and the encore lane can absolutely send one here — the
# channel's most-liked Short is the Antikythera Mechanism, and it already
# became a long-form video that had to pretend a shipwreck was somebody's
# fault. Three chapters are replaced; the rest of the arc carries over
# unchanged, because a human cost, a plan, and a timeline are all true of a
# mystery too — only "who did this on purpose" is not.
_UNSOLVED_SUBSTITUTIONS = {
    "The Claim": (
        "The Popular Theory",
        "This is the chapter the title gets written from — it has to shine "
        "harder than any other chapter in the video. State the most common "
        "or tempting explanation for this mystery — the one most people "
        "already assume, or the first one anybody proposed — as a live "
        "possibility, not a verdict. Build it like a case forming in real "
        "time, not a conclusion announced flatly: name the specific details "
        "that make it tempting one at a time, so the appeal of the theory "
        "visibly builds sentence by sentence. Do not rule it out yet — that "
        "is the rest of the video's job.",
        170),
    "The Decision": (
        "What Has Been Ruled Out",
        "The explanations that have been tested and failed, and why each one does not "
        "fit the evidence. Be specific about what the evidence actually is. This is "
        "where the channel earns its credibility, so never present a theory as settled "
        "and never invent a consensus that does not exist. This is the pivot the whole "
        "arc slows down for: sentences here may run longer than the voice's usual cap "
        "when the extra length lets the evidence actually breathe — up to about 30 "
        "words instead of 20.",
        260),
    "The Verdict": (
        "What Is Still Open",
        "Say plainly whether chapter 2's popular theory survived what chapter 4 ruled "
        "out. Then name the single detail that keeps this genuinely unresolved, and "
        "what would have to be found to settle it. State plainly what is known versus "
        "what is speculated. Only after that, zoom out: name what this specific "
        "mystery reveals about how people make sense of the unexplained in general — "
        "earn this turn, do not assert it cold. If real people were lost or affected, "
        "do not let the zoom-out erase them. End on the open question — do NOT supply "
        "an answer the record does not support, but make it the kind of open question "
        "that makes someone want to know how this channel would chase the next one.",
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
#   The Cost         fast cuts, fast read       — hit hard, earn the scroll-stop
#   The Claim        medium cuts, medium read   — the suspicion forms
#   Supposed To Happen  slow cuts, slower read  — the calm before, and it must
#                                                actually feel calm
#   The Decision     tightest cuts, SLOWEST read — the midpoint reset. Slowing
#                                                down here is the interrupt:
#                                                after two chapters of pace,
#                                                a drop in speed reads as
#                                                "this is the part that matters"
#   The Chain        fastest cuts, fastest read — events overtaking people
#   The Verdict      medium both                — land it
#
# The index is positional, not keyed by heading: THE UNSOLVED substitutes three
# headings but keeps their slots, so a mystery's "What Has Been Ruled Out"
# inherits the pivot treatment its position earns.
# ---------------------------------------------------------------------------

# Which slot in the arc is the midpoint reset. "The Decision" is now the
# fourth chapter (index 3), not the third — the Cost/Claim opener pushed
# everything else back one slot.
PIVOT_INDEX = 3

# words_per_image: lower = more cuts. tts_rate: edge-tts percentage.
PACING = [
    {"role": "open",  "words_per_image": 22, "tts_rate": "+10%"},
    {"role": "claim", "words_per_image": 32, "tts_rate": "+7%"},
    {"role": "build", "words_per_image": 40, "tts_rate": "+6%"},
    {"role": "pivot", "words_per_image": 24, "tts_rate": "+2%"},
    {"role": "drive", "words_per_image": 18, "tts_rate": "+12%"},
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


# Reinstated in the prompt only when CLICKBAIT_MODE=off (see llm_engine.py for
# the flag and the reasoning: the restrained voice is what's on trial, not the
# default). Read once at import — this only needs to react to the env var at
# process start, not mid-run.
_RESTRAINED_VOICE_ADDENDUM = "" if CLICKBAIT_MODE else (
    "RESTRAINED MODE IS ACTIVE (CLICKBAIT_MODE=off) — additionally BANNED: "
    "\"little did they know\", \"what happened next will shock you\", \"to this "
    "day\", \"lost to history\", \"the rest is history\", \"buckle up\", \"let "
    "that sink in\".\n\n"
)

# Same flag, for the packaging call's title prompt.
# Story mode's packaging (docs/VIDEO_SPEC.md): the old rules below were
# written for the courtroom arc ("The Cost", "The Claim") that story mode
# replaced.
STORY_TITLE_RULES = (
    "STORY VIDEO TITLE: under 60 characters. It carries the event's "
    "searchable name (\"Six-Day War\", \"Chernobyl\") AND a stake a "
    "stranger would click for: a number, a turn of events or a question the "
    "video answers (\"13 Days the World Almost Ended | Cuban Missile "
    "Crisis\", \"The Night Chernobyl's Reactor 4 Exploded\" — examples from "
    "other stories, never reuse their words). Neutral: never blame one side of a "
    "present-day conflict. The title and the thumbnail are read together, "
    "so they must say DIFFERENT things.\n")

STORY_THUMB_RULES = (
    "And thumbnail_variants: the words for three thumbnails, ALL CAPS. Each "
    "must make a stranger who has never heard of this story stop scrolling: "
    "ONE shocking, TRUE fact from the narration, in plain words understood "
    "in one second, no names (the faces and tags show who). Use the story's "
    "most shocking figure (dead, destroyed, lost, how fast) with its noun, or "
    "its cruellest twist. Every number must be one the narration states.\n"
    "- faceoff (two leaders face each other): big = the shocking figure and "
    "its noun, 2-3 words; box = 2-4 words that make it hit.\n"
    "- person (the one who decided, alone): setup = what they did, 2-4 "
    "words; twist = what it cost them, 2-4 words.\n"
    "- map (the place, with an arrow): question = 2-5 words a stranger needs "
    "answered; tag = the shocking figure again, 2-3 words.\n"
    "The shape, from a different story (never reuse these words): faceoff "
    "big \"13 DAYS\", box \"ONE STEP FROM WAR\"; person setup \"HE SENT THE "
    "MISSILES\", twist \"THEN TOOK THEM BACK\"; map question \"90 MILES "
    "FROM FLORIDA?\", tag \"13 DAYS OF FEAR\".\n"
    "Neutral about present-day conflicts: shock with facts, never blame.\n")

def _checked_thumb_texts(variants, story, full_text):
    """The thumbnail words with every field whose numbers are not in the fact
    sheet or the narration removed: a thumbnail's shocking figure must be
    one the video itself states."""
    if not isinstance(variants, dict):
        return {}
    known = json.dumps((story or {}).get("sheet") or {}) + " " + (full_text or "")
    digits = {d.replace(",", "") for d in re.findall(r"\d[\d,]*", known)}
    out = {}
    for look, fields in variants.items():
        if not isinstance(fields, dict):
            continue
        kept = {}
        for k, v in fields.items():
            nums = [d.replace(",", "") for d in re.findall(r"\d[\d,]*", str(v or ""))]
            if all(n in digits for n in nums):
                kept[k] = v
            else:
                print(f"   ↩️  Thumbnail {look}.{k} dropped: {v!r} has a number "
                      f"the video does not state.")
        out[look] = kept
    return out


# words the examples use: a variant made of them is a copy, not a thumbnail
THUMB_EXAMPLE_TEXTS = {"13 DAYS", "ONE STEP FROM WAR", "HE SENT THE MISSILES",
                       "THEN TOOK THEM BACK", "90 MILES FROM FLORIDA?",
                       "90 MILES FROM FLORIDA", "13 DAYS OF FEAR"}

_RESTRAINED_TITLE_ADDENDUM = "" if CLICKBAIT_MODE else (
    "BANNED, because they would fit any story at all: 'the hidden ...', "
    "'the untold story', 'what really happened', 'the forgotten ...', "
    "'the secret ...', 'the truth about', 'shocking', 'insane'.\n")

SYSTEM_PROMPT = f"""You write documentary narration for The Archive Paradox, a YouTube \
channel about catastrophic decisions: one order, one design flaw, one miscalculation, \
and the disaster it caused.

You write about REAL, verifiable events. Never invent facts, dates, numbers or quotes. \
If you are unsure of a specific figure, describe it qualitatively instead of guessing.

VOICE:
- Calm, factual, forensic. The events supply the drama; the narration does not add any.
- Short declarative sentences. Maximum 20 words each, UNLESS this chapter's own \
brief below explicitly grants more room — a chapter-specific limit always overrides \
this default one.
- Write for the ear: no parentheses, no semicolons, no bulleted lists.
- Concrete specifics over adjectives. "Forty tonnes of propellant" beats "an enormous amount".
- Do not address the audience. No "you might be wondering". No "we".

PLAIN LANGUAGE (violating this is why a technical chapter loses the viewer even \
though every sentence is short):
- Write for someone who has NEVER heard of this event and knows nothing about the \
period or the field. This is a documentary for a general audience, not a briefing \
for specialists.
- Any technical, military, engineering or period-specific term — "tang and clevis \
joint", "joint rotation", "a salvo", "a flight readiness review" — must be explained \
in plain words in the same sentence it first appears, or replaced with the plain \
description outright. A reader should never have to already know the field to \
follow the mechanism.
- This applies to the MECHANISM the whole video turns on most of all: if the \
viewer cannot repeat, in their own words, what actually failed and why, the \
chapter has not done its job no matter how accurate it is.

{_RESTRAINED_VOICE_ADDENDUM}
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

# A model returning the identical 400 "invalid argument" on every attempt is
# not a fluke either — it means this key's request shape is incompatible with
# that model (a param it does not accept, a deprecated ID that still 200s on
# /models but 400s on /chat/completions). Found live: gemini-3.6-flash failed
# this way on every one of six chapters in one run and was never dropped,
# because only 404 and 429/quota errors were retired — burning a request per
# attempt for a model that had already proven it could never succeed.
MAX_BAD_REQUEST_FAILURES = 2
_bad_request_failures = {}

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
    """Drops models that have already proved they cannot return JSON today,
    or that this key cannot successfully call at all."""
    live = [m for m in models
            if _parse_failures.get(m, 0) < MAX_PARSE_FAILURES
            and _bad_request_failures.get(m, 0) < MAX_BAD_REQUEST_FAILURES
            and m not in _retired]
    # If every model has been written off, try them all again rather than
    # returning nothing: a degraded reply still beats no reply.
    return live or list(models)


def _interleave_by_provider(models, pool):
    """Groups models by provider, in first-seen order, and interleaves them
    round-robin (one from each provider in turn) rather than leaving them
    grouped. Gemini alone contributes several model variants and is always
    first in pool.models, so a flat walk down the list spends the entire
    MAX_MODELS_PER_CALL budget inside Gemini whenever all of it is briefly
    down at once (a real run hit exactly this: four straight Gemini 503s,
    "high demand", while groq/cerebras/zhipu sat fully untried and working)
    — one provider's outage should never be able to cost a call every slot
    it has.
    """
    from llm_provider import SECOND_KEY_SUFFIX
    groups, order = {}, []
    # overloaded models (503) go to the back of their group, so the next
    # call tries a sibling instead of the same busy model again
    models = sorted(models, key=lambda m: m in _overloaded)
    for m in models:
        # each Gemini key is its own group: grouped by provider alone, the
        # second key's models sat behind the first key's and never got one
        # of the call's slots (15 Oct: key 2 untouched while key 1 was spent)
        p = pool.provider_for(m) + (SECOND_KEY_SUFFIX if m.endswith(SECOND_KEY_SUFFIX) else "")
        if p not in groups:
            groups[p] = []
            order.append(p)
        groups[p].append(m)

    result, idx, empty_streak = [], 0, 0
    while len(result) < len(models) and empty_streak < len(order):
        p = order[idx % len(order)]
        if groups[p]:
            result.append(groups[p].pop(0))
            empty_streak = 0
        else:
            empty_streak += 1
        idx += 1
    return result


_last_model = None
_overloaded = set()


# ---------------------------------------------------------------------------
# The gateway (2026-10-01): every request is checked on the way out (does the
# prompt fit this model?) and every reply on the way back (is it real?). It
# replaces the one-off patches each step used to carry: a "..." title, an
# empty maps list, a 116,000-character article sent to models that refuse it.
# ---------------------------------------------------------------------------

# What a free model will take in one request, in tokens (prompt plus reply
# room). Conservative starting points; a "request too large" answer lowers a
# model's own figure for the rest of the run (_size_limits).
PROVIDER_REQUEST_TOKENS = {"gemini": 900_000, "anthropic": 180_000, "cloudflare": 120_000,
                           "zhipu": 120_000, "openrouter": 30_000,
                           "groq": 30_000, "cerebras": 30_000}
_size_limits = {}
_junk_replies = {}


def _request_tokens(system, user, max_tokens):
    """A rough token count for a request: ~3.5 characters a token, plus the
    reply room the free tiers count against the same limit."""
    return int((len(system or "") + len(user or "")) / 3.5) + min(int(max_tokens or 0), 4000)


def _fits(pool, model, need):
    limit = _size_limits.get(model) or PROVIDER_REQUEST_TOKENS.get(pool.provider_for(model), 30_000)
    return need <= limit


def _is_too_large(err):
    low = err.lower()
    return ("413" in err or "request too large" in low or "context length" in low
            or "maximum context" in low or "too many tokens" in low
            or "reduce the length" in low)


def _placeholder(text):
    """A template echo rather than an answer: "...", "…", "[[phrase]]"."""
    bare = text.replace("...", " ").replace("\u2026", " ")
    return (("..." in text or "\u2026" in text) and len(re.findall(r"[A-Za-z0-9]", bare)) < 3) \
        or "[[phrase]]" in text


def _first_placeholder(data, path=""):
    if isinstance(data, str):
        return path or "reply" if _placeholder(data) else None
    if isinstance(data, dict):
        for k, v in data.items():
            got = _first_placeholder(v, f"{path}.{k}" if path else k)
            if got:
                return got
    if isinstance(data, list):
        for i, v in enumerate(data):
            got = _first_placeholder(v, f"{path}[{i}]")
            if got:
                return got
    return None


def _chapter_reply_problem(data):
    """A chapter reply needs segments, each with narration and an image
    prompt; the chapter checks (length, simplicity, structure) come after."""
    segs = data.get("segments") if isinstance(data, dict) else None
    if not isinstance(segs, list) or not segs:
        return "no segments"
    for i, sg in enumerate(segs):
        if not isinstance(sg, dict) or not isinstance(sg.get("narration"), str) \
                or len(sg["narration"].split()) < 3:
            return f"segment {i} has no narration"
    return None


def _reply_problem(data, expect=None):
    """Why a parsed reply cannot be used, or None. Every reply is checked for
    template echoes; `expect` adds what this step needs: a callable returning
    the problem, or {key: rule} where rule is str (a real string), list (a
    non-empty list), ("list", n) (at least n items), dict or int."""
    if data is None:
        return "no JSON"
    where = _first_placeholder(data)
    if where:
        return f"placeholder text in {where}"
    if expect is None:
        return None
    if callable(expect):
        return expect(data)
    if not isinstance(data, dict):
        return "not a JSON object"
    for key, rule in expect.items():
        v = data.get(key)
        if rule is str and not (isinstance(v, str) and len(re.findall(r"[A-Za-z0-9]", v)) >= 2):
            return f"no {key}"
        if rule is list and not (isinstance(v, list) and v):
            return f"empty {key}"
        if isinstance(rule, tuple) and rule[0] == "list" and not (
                isinstance(v, list) and len(v) >= rule[1]):
            return f"{key} has {len(v) if isinstance(v, list) else 0} item(s), {rule[1]} needed"
        if rule is dict and not isinstance(v, dict):
            return f"no {key}"
        if rule is int:
            try:
                int(v)
            except (TypeError, ValueError):
                return f"no {key}"
    return None


def _order_models(pool, need=0):
    """Preferred model first, then the rest round-robined across providers,
    capped so one call cannot walk the entire list. Models this request is
    too large for are left out before the cap, so they never use a slot."""
    from llm_provider import LAST_RESORT_PROVIDERS
    models = [m for m in _usable_models(pool.models) if not need or _fits(pool, m, need)]
    # Cloudflare shares its free allowance with the AI images: one of its
    # models is tried, after every other model of this call has failed.
    last = [m for m in models if pool.provider_for(m) in LAST_RESORT_PROVIDERS]
    models = [m for m in models if m not in last]
    if _preferred_model and _preferred_model in models:
        rest = [m for m in models if m != _preferred_model]
        ordered = [_preferred_model] + _interleave_by_provider(rest, pool)
    else:
        ordered = _interleave_by_provider(models, pool)
    return ordered[:MAX_MODELS_PER_CALL] + last[:1]


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


def _call(pool, system, user, max_tokens, skip=(), expect=None):
    """Runs a prompt down the model chain, one attempt per model.

    The chain spans providers: Gemini's models first, then OpenRouter's free
    ones, each with its own client. A run used to die outright when Gemini
    answered 404 and 429, despite a working OpenRouter key being configured.

    Raises QuotaExhausted the moment a daily allowance is gone, instead of
    walking the remaining models to collect identical 429s.

    skip: models not to ask this time, for a caller retrying because the
    last model's (parseable) answer was unusable. _last_model records which
    model produced the returned data, so the caller knows what to skip.
    """
    global _preferred_model, _request_count, _last_model
    _last_model = None

    if not pool:
        raise RuntimeError("No LLM provider configured: set GEMINI_API_KEY "
                           "or OPENROUTER_API_KEY.")

    # A model that is out of quota is out for the run, not just this call.
    # Without this every chapter re-tried the same exhausted model first and
    # paid the same 429 before moving on.
    exhausted = set()
    need = _request_tokens(system, user, max_tokens)
    order = _order_models(pool, need)
    if not order and _usable_models(pool.models):
        print(f"   ⚠️ This request (~{need:,} tokens) is larger than every working "
              f"model takes.")

    for model in order:
        if model in exhausted or model in skip:
            continue
        client = pool.client_for(model)
        provider = pool.provider_for(model)
        try:
            _request_count += 1
            kwargs = dict(
                model=pool.api_name(model),
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

            problem = _reply_problem(data, expect)
            if problem:
                # parsed but not usable: the next model is asked, and a model
                # that keeps sending junk is dropped like one sending bad JSON
                _log_raw_failure(model, content, problem, finish_reason)
                _junk_replies[model] = _junk_replies.get(model, 0) + 1
                if _preferred_model == model:
                    _preferred_model = None
                if _junk_replies[model] >= MAX_PARSE_FAILURES:
                    _retired.add(model)
                print(f"   ↩️  {model}: reply rejected ({problem}) — asking another model.")
                continue

            if _preferred_model != model:
                print(f"   📌 using {model} for the rest of this run")
                _preferred_model = model
            _last_model = model
            return data
        except Exception as e:
            err = str(e)
            if _is_too_large(err):
                # not this model's day, this request's size: remember the
                # model's real limit and move on, without counting a failure
                _size_limits[model] = int(need * 0.8)
                print(f"   ↩️  {model}: request too large (~{need:,} tokens) — "
                      f"next model.")
                continue
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
            if "503" in err or "high demand" in err.lower():
                _overloaded.add(model)
            if _parse_failures.get(model, 0) >= MAX_PARSE_FAILURES:
                print(f"   ⛔ dropping {model} for the rest of this run "
                      f"({MAX_PARSE_FAILURES} unparseable replies).")
            # A retired model never comes back mid-run, and a model out of its
            # daily quota will not recover either. Retiring both here is what
            # keeps six chapters from each paying the same two failures.
            if "403" in err and ("denied" in err.lower() or "permission" in err.lower()):
                # the whole key is refused ("Your project has been denied
                # access"): every model it serves goes, not one retry each —
                # the 2026-09-30 weekly run paid a dozen retries for it
                client = pool.client_for(model)
                gone = [m for m in pool.models if pool.client_for(m) is client]
                _retired.update(gone)
                print(f"   ⛔ this key is refused (403) — dropping its {len(gone)} "
                      f"model(s) for the rest of this run.")
            elif "404" in err or "no longer available" in err.lower():
                _retired.add(model)
                print(f"   ⛔ {model} is not available to this key — dropped.")
            elif "429" in err or "quota" in err.lower():
                _retired.add(model)
                print(f"   ⛔ {model} is out of quota — dropped for this run.")
                time.sleep(5)
            elif "400" in err or "invalid argument" in err.lower():
                # Not a fluke: this key's request shape does not work against
                # this model at all, and every retry pays the same failure.
                # See MAX_BAD_REQUEST_FAILURES above for the run that found this.
                _bad_request_failures[model] = _bad_request_failures.get(model, 0) + 1
                if _bad_request_failures[model] >= MAX_BAD_REQUEST_FAILURES:
                    _retired.add(model)
                    print(f"   ⛔ dropping {model} for the rest of this run "
                          f"({MAX_BAD_REQUEST_FAILURES} bad-request errors).")
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

def _story_ending_rule(next_heading, hook) -> str:
    """Story mode: a chapter ends on a hook only where the outline earned
    one (at most two per video). The 1967 video ended every chapter on a
    teaser, and by the second one viewers stopped believing them."""
    if not next_heading:
        return _handoff_rule(None)
    if hook:
        return ("END this chapter on this unresolved fact, in one flat "
                f"sentence under 14 words: {hook}\n\n")
    return ("END this chapter on its last beat itself. No teaser, no "
            "foreshadowing of the next chapter, no summary.\n\n")


def _handoff_rule(next_heading) -> str:
    """The instruction that turns a chapter ending into an open loop."""
    if not next_heading:
        return ("This is the FINAL chapter. Close the video. Land it on either a "
                "stated lesson or a genuinely open question the evidence itself "
                "leaves unresolved — not a tease, an actual ending. If you close "
                "on a question, it must be one this story's own facts leave "
                "open, never a hook for a video that does not exist.\n\n")
    ban_line = (
        "BANNED in this sentence: \"but\" as the opening word, \"little did "
        "they know\", \"what happened next\", \"that's not even\", \"stay "
        "with me\", \"we'll get to that\", \"more on that\", \"as we will "
        "see\". Never address the viewer.\n\n"
        if not CLICKBAIT_MODE else "")
    return (
        f"HAND OFF TO THE NEXT CHAPTER, which is titled \"{next_heading}\".\n"
        "Your LAST sentence must leave one thing unresolved, so the viewer needs "
        "the next chapter to settle it — a document nobody had read, an order "
        "already signed, a person already on their way, a number that does not "
        "yet add up, or a direct tease of what's coming, whichever hits hardest. "
        "Keep it under 14 words.\n"
        "It must NOT summarise this chapter, and it must NOT be a question.\n"
        f"{ban_line}")


def _chapter_prompt(topic, heading, brief, words, previous_tail, feedback="",
                    era_year=None, covered=None, episode_format=None,
                    next_heading=None, words_per_image=None, story_hook=None,
                    segment_rule=None):
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
        f"{int(words * 0.8)}. "
        + (f"{segment_rule}\n\n" if segment_rule else
           f"Split it into exactly "
           f"{n_images} consecutive segments; each segment gets its own image prompt. "
           f"Each segment should be roughly {max(20, words // n_images)} words — do not "
           f"write one-line segments.\n\n") +
        f"{_archive_query_rule(era_year)}\n\n"
        "In exactly ONE segment's narration, mark the single punchiest short "
        f"phrase — {MIN_PUNCH_WORDS} to {MAX_PUNCH_WORDS} words, verbatim, exactly as "
        "you wrote them — by wrapping it in double square brackets. It will flash "
        "on screen as a bold callout timed to the instant you say it, so pick "
        "whatever would hit hardest that way: a number, a name, a stark "
        "consequence — never a full clause or a restatement of the sentence "
        "around it. The brackets are removed before the narration is spoken, so "
        "the sentence must read completely naturally without them.\n"
        "Example: \"...and the order [[killed six hundred men]] before dawn.\"\n"
        "If nothing in this chapter is punchy enough for a callout, mark "
        "nothing — never force one.\n\n"
        "Every sentence must carry information the viewer did not have a "
        "sentence ago. Never pad by restating an earlier line in new words.\n\n"
        f"{_story_ending_rule(next_heading, story_hook) if story_hook is not None else _handoff_rule(next_heading)}"
        "Output ONLY a JSON object, no markdown fences. Replace every "
        "placeholder below with material from this story:\n"
        '{"segments": [{"narration": "<narration, with at most one '
        '[[punch phrase]] marked inline>", '
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

# 2010 since 2026-09-26 (was 2005), the owner asking for "as close to the
# present as stays safe". Roughly fifteen years back is where an event is
# both settled (its war or crisis has ended, its inquiries have reported)
# and still densely covered in every free model's training data; past it,
# the models' knowledge thins and the dispute is often still live. Moving
# it later should keep that gap: 2020-era events (the war in Yemen since
# 2015, Syria, Crimea) are exactly the live disputes this rule exists for.
HISTORY_CUTOFF_YEAR = int(os.getenv("HISTORY_CUTOFF_YEAR", "2010"))

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


# The old key_stat design asked the model for a separate "value + label"
# JSON field and then had to search the narration afterward to find out
# whether it was ever actually said — which it wasn't, once, on a real
# render ("83" as key_stat, "eighty competing nationalities" as what the
# narration actually said — a true, related, but different figure). No
# search can fix a fact that was never spoken. This replaces it: the model
# marks a short phrase INLINE, inside the narration it is already writing,
# so the callout is a verbatim quote of the same words the audio will say —
# grounded by construction, not validated after the fact.
MIN_PUNCH_WORDS = 2
MAX_PUNCH_WORDS = 6

_PUNCH_RE = re.compile(r"\[\[(.+?)\]\]")


def _extract_punch_phrase(segs):
    """Finds the model's marked punch phrase across a chapter's segments and
    strips the [[ ]] markers from every segment's narration so they are
    never spoken or captioned — this must run even when nothing is used as
    the callout, or a model that ignores "exactly ONE" leaks literal
    brackets into the published narration.

    Returns (phrase, segment_index) for the first marked phrase that is
    within MIN_PUNCH_WORDS..MAX_PUNCH_WORDS words, or (None, None) if the
    model marked nothing usable.
    """
    phrase, idx = None, None
    for i, s in enumerate(segs):
        text = s.get("narration", "")
        matches = _PUNCH_RE.findall(text)
        if matches and phrase is None:
            candidate = matches[0].strip()
            n_words = len(candidate.split())
            if MIN_PUNCH_WORDS <= n_words <= MAX_PUNCH_WORDS:
                phrase, idx = candidate, i
        if matches:
            s["narration"] = _PUNCH_RE.sub(lambda m: m.group(1), text)
    return phrase, idx


_ACTOR_FILLER = {"the", "of", "and", "movement", "government", "forces",
                 "state", "people", "republic", "kingdom", "party"}


def _actors_for(candidates, data):
    """The main actors of the headline the model chose: the ones channel
    watch extracted for that subject when it has them (more reliable than
    the anchor call grading its own answer), else the anchor's own
    "actor"."""
    chosen = (data.get("subject") or "").lower()
    for c in candidates or []:
        subj = (c.get("subject") or "").lower()
        if c.get("actors") and subj and (subj in chosen or chosen in subj):
            return c["actors"]
    actor = (data.get("actor") or "").strip()
    return [actor] if actor else []


def _anchor_names_actor(actors, topic: str, why: str) -> bool:
    """The anchor must name one of the headline's main actors (prefix-matched,
    so Houthi/Houthis or Yemen/Yemeni count). This is the check the 1967
    Straits of Tiran pick failed: the headline was about the Houthis, and
    "Red Sea" alone was enough to pass _anchor_is_relevant. No actors known
    means nothing to check."""
    words = [w for a in (actors or []) for w in re.findall(r"[a-z]+", a.lower())
             if len(w) >= 4 and w not in _ACTOR_FILLER]
    if not words:
        return True
    haystack = f"{topic} {why}".lower()
    return any(w[:5] in haystack for w in words)


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


# Story mode (long form) tells a whole event with people on each side and a
# turning point, not only one decision: a war, a crisis, a disaster. The
# 2026-09-29 run rejected the 1948 war for its Gaza theme under the Shorts
# rule and fell back to a news headline the watched channel never covered.
STORY_MARKERS = NICHE_MARKERS + (
    "war", "battle", "siege", "invasion", "invaded", "uprising", "revolt",
    "rebellion", "revolution", "crisis", "coup", "massacre", "intifada",
    "blockade", "offensive", "occupation", "occupied", "conflict", "attack",
    "bombing", "evacuation", "famine", "explosion", "meltdown", "sinking",
    "fire", "fired", "shelled", "voyage", "expedition", "raid", "incident",
    "accident", "mutiny", "assassination", "assassinated", "riot", "flood",
    "earthquake", "fleet", "shipwreck", "crash", "collapse", "trial", "purge",
    "d-day", "landing", "landings", "campaign", "operation", "hanged",
    "executed", "execution", "persecution", "hunt", "exodus", "hijacking")

STORY_ANCHOR_RULE = (
    "- THE STORY MUST BE A STORY: a war, battle, siege, uprising, crisis, "
    "coup or disaster, with named people on each side and a turning point, "
    "that a 12-minute documentary can tell from start to end. Not a law, a "
    "treaty, a plan or a general overview on its own.\n"
    "  GOOD: 'the 1948 Arab-Israeli War', 'the 1962 Cuban Missile Crisis', "
    "'the 1986 Chernobyl disaster'.\n"
    "  BAD:  'the 1947 UN Partition Plan' — a document, not a story with a "
    "turning point.\n")


def idea_to_event(pool, idea, avoid_titles=()):
    """The one real event that best tells an idea as a story, as a topic
    sentence, or None. Story mode needs an event (sides, dates, a turning
    point); an automatic lane sometimes hands it an idea ("Maimonides'
    principle that it is better to acquit a thousand guilty..."), which is a
    good subject told the wrong way. The event keeps the idea as its hook:
    the Salem witch trials (1692), where Increase Mather wrote that it was
    better that ten suspected witches escape than one innocent be condemned.
    """
    avoid = "\n".join(f"- {t}" for t in list(avoid_titles or [])[:20] if t)
    prompt = (
        f"This documentary subject is an IDEA, not an event:\n{idea}\n\n"
        "Name the ONE real historical event that best tells this idea as a "
        "story: a trial, war, siege, crisis, uprising or disaster with named "
        "people on opposite sides, dates and a turning point, where this idea "
        "was at stake or was put to the test. It must be well documented on "
        f"English Wikipedia and have ended in {HISTORY_CUTOFF_YEAR} or earlier. "
        "Pick the most dramatic fit, not merely the earliest.\n"
        + (f"ALREADY COVERED ON THIS CHANNEL — choose something else:\n{avoid}\n"
           if avoid else "")
        + "If no real event fits, answer with an empty event.\n"
        'Output ONLY JSON: {"event": "the event\'s common name, e.g. Salem '
        'witch trials", "year": 1692, "topic": "one sentence: the event, and '
        'how it put the idea to the test", "why": "one sentence"}')
    try:
        data = _call(pool, "You pick historical events for documentaries. "
                     "Output only JSON.", prompt, max_tokens=2000)
    except QuotaExhausted:
        raise
    except Exception as e:
        print(f"   ↩️  Idea-to-event request failed ({type(e).__name__}: {e}).")
        return None
    event = str((data or {}).get("event") or "").strip()
    topic = str((data or {}).get("topic") or "").strip()
    try:
        year = int((data or {}).get("year") or 9999)
    except (TypeError, ValueError):
        year = 9999
    if not event or not topic or "..." in event:
        return None
    if year > HISTORY_CUTOFF_YEAR:
        print(f"   ↩️  Event {event!r} ({year}) is after the {HISTORY_CUTOFF_YEAR} cutoff.")
        return None
    sentence = f"{event} ({year}): {topic}"
    if not _anchor_fits_niche(sentence, story=True):
        print(f"   ↩️  {event!r} does not read as an event either.")
        return None
    return sentence


def _anchor_fits_niche(topic: str, why: str = "", story: bool = False) -> bool:
    """True if the chosen event is a decision-and-consequence story (story
    mode: any war, crisis or disaster, see STORY_MARKERS).

    Deliberately checked against the topic sentence rather than the subject: the
    trending subject is a headline and is allowed to be anything. What has to
    fit the channel is the story we tell about it.
    """
    text = f"{topic} {why}".lower()
    if not story:
        return any(re.search(rf"\b{m}\b", text) for m in NICHE_MARKERS)
    # plurals count too: "trials", "wars", "landings"
    return any(re.search(rf"\b{re.escape(m)}(?:s|es)?\b", text) for m in STORY_MARKERS)


def extract_channel_subjects(videos, pool=None, avoid_titles=()):
    """Turns a watched channel's recent uploads into ranked, generic
    documentary subjects. Returns [{"subject", "score", "source"}, ...],
    most important first, or [] if nothing qualifies or the call fails.

    This replaces a fixed keyword-bucket list that had two real problems: it
    needed hand-curation for every new recurring story (no bucket at all for
    Yemen/the Houthis, for instance), and ties between buckets that both
    matched went to whichever bucket happened to be listed first — not
    whichever was actually the segment's real subject. Reading the text
    directly lets the model name the actual subject and weigh stories
    against each other instead of guessing from isolated keywords.

    That means this call reads raw titles and descriptions, which is exactly
    what channel_watch.py's docstring warns is dangerous on a show that
    mixes real geopolitical themes with a named living guest's personal
    conduct in the same segment — so the prompt below carries the identical
    "never a named living private individual's personal conduct" rule
    select_historical_anchor enforces downstream, and is instructed to drop
    those uploads entirely rather than label them even generically. This is
    the first of two gates, not the only one: anything that slips through
    still has to clear select_historical_anchor's own hard rules before it
    can become a topic.
    """
    if not videos:
        return []
    pool = pool or get_pool()

    avoid_block = ""
    recent = [t for t in (avoid_titles or []) if t][:20]
    if recent:
        listed = "\n".join(f"- {t}" for t in recent)
        avoid_block = ("\nALREADY COVERED ON THIS CHANNEL — do not surface a "
                       f"subject any of these is about:\n{listed}\n")

    # Raw view counts across uploads of different ages favour the oldest one,
    # so each count carries its age and the model can compare like with like.
    now = datetime.datetime.now(datetime.timezone.utc)
    lines = []
    for i, v in enumerate(videos, 1):
        views = v.get("view_count")
        age = ""
        try:
            posted = datetime.datetime.strptime(
                v.get("published_at", ""), "%Y-%m-%dT%H:%M:%SZ"
            ).replace(tzinfo=datetime.timezone.utc)
            age = f", {max(1, round((now - posted).total_seconds() / 3600))}h old"
        except ValueError:
            pass
        views_str = f" ({views:,} views{age})" if views else ""
        desc = (v.get("description") or "")[:600]
        lines.append(f"[{i}] TITLE: {v.get('title', '')}{views_str}\n"
                     f"DESCRIPTION: {desc}")
    listing = "\n\n".join(lines)

    prompt = (
        "Here are recent uploads from a political talk show:\n\n" + listing +
        "\n\n"
        "For EACH upload, decide whether it covers a genuine, settled-enough "
        "geopolitical, policy or historical subject a documentary channel "
        "could responsibly build an episode around — or whether it is "
        "really about a named living person's personal conduct, health, "
        "character, relationships, or an unverified/ongoing allegation "
        "against them (an affair, an addiction, a defamation dispute, a "
        "family feud, a lawsuit over a book, a health rumor). DROP every "
        "upload of that second kind entirely — do not name the people "
        "involved or describe the dispute, not even generically. This "
        "applies no matter how newsworthy the dispute is.\n\n"
        "For every upload that qualifies, write a SHORT, GENERIC subject "
        "label naming the recurring real-world theme — not the show's "
        "framing, not a guest's specific claim, not today's headline. Two "
        "segments about the same underlying story should produce the SAME "
        "label.\n"
        "  GOOD: 'the Houthi campaign against Red Sea shipping'\n"
        "  GOOD: 'Trump-Xi summitry and the Taiwan question'\n"
        "  BAD:  'Piers grills Houthi spokesman on Mecca attack claim' (a "
        "take on the segment, not the underlying subject)\n"
        "  BAD:  any label built around a living private individual's "
        "personal dispute — drop it instead, per the rule above\n\n"
        "Then RANK the qualifying subjects by real-world importance and "
        "consequence, most important first — not by which is safest or "
        "most familiar. Weigh how many people or countries are materially "
        "affected and how significant the immediate stakes are; use view "
        "count, relative to the upload's age, only as a tiebreaker between "
        "otherwise comparable stories.\n"
        f"{avoid_block}\n"
        "For each subject also give actors: the MAIN group or state at the "
        "centre of it and its country (e.g. [\"Houthis\", \"Yemen\"]) — who "
        "is acting, not where it happens.\n"
        'Output ONLY JSON: {"subjects": [{"label": "...", "why_important": '
        '"one sentence", "actors": ["...", "..."]}, ...]}, ranked most '
        "important first. Omit anything that does not qualify. Use an empty "
        "list if nothing qualifies."
    )

    data = _call(pool,
                "You find documentary-worthy subjects in the news. Output "
                "only JSON.",
                prompt, max_tokens=3000)
    if not data or not data.get("subjects"):
        return []

    subjects = [s for s in data["subjects"] if (s.get("label") or "").strip()]
    n = len(subjects)
    return [{"subject": s["label"].strip(),
             "why": (s.get("why_important") or "").strip(),
             "actors": [a.strip() for a in (s.get("actors") or [])
                        if isinstance(a, str) and a.strip()][:3],
             "score": n - i, "source": "channel_watch"}
            for i, s in enumerate(subjects)]


def select_historical_anchor(candidates, pool=None, avoid_titles=(),
                             attempts: int = 2, ranked: bool = False,
                             story: bool = False):
    """Maps a trending subject onto a historical event old enough to be settled.

    Returns {"subject", "topic", "anchor_year", "why"} or None. None is not a
    failure — the caller falls back to the normal topic rotation, which is
    always preferable to publishing something the guardrail could not clear.

    The trend supplies the subject; the niche supplies the story. An anchor that
    is merely relevant history is rejected the same way one past the cutoff year
    is, and re-asked once with the reason, because dropping to the rotation
    throws away the only reason this lane exists.

    ranked=True means the candidates arrive most important first (channel
    watch ranks them by real-world consequence) and that order must decide
    the pick: the model takes the first subject it can anchor and only moves
    down the list when a higher one has no qualifying history. Without it,
    the "richest documentary history" rule below silently overrode that
    ranking and the extraction call's ordering had no effect at all.
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

    if ranked:
        listing = "\n".join(
            f"{i}. {c['subject']}" + (f" — {c['why']}" if c.get("why") else "")
            for i, c in enumerate(candidates[:10], 1))
        pick_rule = (
            "These are RANKED, most important first. Take subject 1 unless no "
            "historical event meeting the rules below explains it; only then "
            "move to 2, and so on. Do not skip a higher subject because a lower "
            "one has a more famous or more dramatic history. Name the "
            "historical event that explains THE SPECIFIC SITUATION IN THAT "
            "HEADLINE.\n\n")
    else:
        listing = "\n".join(f"- {c['subject']}" for c in candidates[:10])
        pick_rule = (
            "Pick the ONE with the richest documentary history behind it, then "
            "name the historical event that explains THE SPECIFIC SITUATION IN "
            "THAT HEADLINE.\n\n")
    prompt = (
        "Here are subjects currently in the news:\n\n" + listing + "\n\n"
        + pick_rule +
        "RELEVANCE IS THE POINT. The viewer arrives asking why this is "
        "happening. The event you choose must be the origin of that exact "
        "dispute, border, alliance or grievance — not a famous episode that "
        "merely happens to involve the same country.\n"
        "THE MAIN ACTOR DECIDES IT, NOT THE PLACE. The event must be about the "
        "group or state at the centre of the headline, or its direct origin. "
        "Sharing only the sea, strait or region is not relevance:\n"
        "- Headline about the Houthi campaign against Red Sea shipping -> the "
        "origin of the Houthis in Yemen (a Yemeni decision that created or "
        "hardened the movement). NOT another country's Red Sea blockade, "
        "which happened in the same water and explains nothing about the "
        "Houthis.\n"
        "- Headline about Russian strikes on Ukraine -> the 1994 Budapest "
        "Memorandum, or the 1954 transfer of Crimea. NOT the Trans-Siberian "
        "Railway, which involves Russia but explains nothing in the headline.\n"
        "- Headline about the Spanish-Moroccan border -> the 1415 capture of "
        "Ceuta and the contested sovereignty that followed. NOT the Spanish "
        "Armada.\n"
        "- Headline about the Strait of Hormuz -> the 1980s tanker war. NOT the "
        "construction of Persepolis.\n"
        + (("- Headline about Israel and Gaza or the West Bank -> the 1948 "
            "Arab-Israeli War, the 1956 Suez Crisis, the 1973 Yom Kippur War "
            "or the First Intifada, whichever best explains the specific thing "
            "in the headline. NOT a Biblical-era episode, which predates the "
            "modern dispute entirely and explains nothing about why it exists "
            "now.\n\n") if story else
           ("- Headline about Israel and Gaza or the West Bank -> the 1947 UN "
            "Partition Plan, the 1949 Armistice Lines, or the 1993 Oslo Accords, "
            "whichever actually explains the specific thing in the headline (a "
            "border dispute points at 1949; a settlement or peace-process question "
            "points at 1993). NOT a Biblical-era episode, which predates the "
            "modern dispute entirely and explains nothing about why it exists now.\n\n")) +
        "HARD RULES:\n"
        f"- The event must have CONCLUDED in {HISTORY_CUTOFF_YEAR} or earlier. "
        "Anything more recent is rejected.\n"
        "- Pick a settled, documented event, not a live dispute.\n"
        + (STORY_ANCHOR_RULE if story else
           ("- THE STORY MUST BE THIS CHANNEL'S KIND OF STORY: one decision, order, "
            "refusal or miscalculation, and the disaster that followed it. Not a "
            "law, not a treaty, not a general overview of a conflict, unless the "
            "point of the video is the choice someone made and what it cost.\n"
            "  GOOD: 'the 1918 decision to scuttle the Zeebrugge blockships in the "
            "wrong position, which left the canal open'.\n"
            "  BAD:  'the 1963 Swiss law requiring a bunker in every house' — real "
            "history, but nobody decided anything that went wrong.\n")) +
        f"{avoid_block}"
        "- Do not take a side, assign blame between present-day states, or "
        "describe current politics. You are choosing a history topic, not "
        "commenting on the news. This applies with no exceptions to Israel, "
        "Palestine, Russia, Ukraine and Iran: pick the documented root cause, "
        "narrate what happened and what it cost, and stop there.\n"
        "- NEVER choose a topic centred on a named living private individual's "
        "personal conduct, health, character or relationships (an affair, an "
        "addiction, a family dispute, an unverified allegation) — even if that "
        "is what the headline is actually about. Only a settled public event "
        "with a documented decision behind it qualifies. If nothing in the "
        "candidates clears that bar, say so instead of forcing one.\n"
        + ("" if story else
           "- The topic must fit this channel: a decision, order or miscalculation "
           "and what it cost.\n") + "\n"
        'Output ONLY JSON: {"subject": "the headline you chose", "actor": "the '
        'main group or state at the centre of that headline", "topic": "one '
        'specific sentence describing the historical event", "name": "the '
        'event\'s common name in 2-6 words, no year, e.g. The Capture of '
        'Ceuta", "anchor_year": 1415, "why": "one sentence naming the '
        'countries or places involved and how this event produced the '
        'situation in the headline"}'
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
                     prompt + feedback, max_tokens=3000, expect={"topic": str})
        if not data or not data.get("topic"):
            # A total non-answer (every model this call tried came back
            # empty or erroring) is exactly the transient case worth using
            # the remaining attempt budget on — the previous version gave
            # up immediately here, so a single bad round of provider
            # errors early in a run (before _order_models had a reason to
            # spread across providers) could silently drop a channel-watch
            # match that a second attempt would have recovered.
            print(f"⚠️ No usable anchor returned (attempt {attempt}/{attempts}).")
            if attempt >= attempts:
                return None
            continue

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
        elif not _anchor_names_actor(_actors_for(candidates, data),
                                     data.get("topic", ""), data.get("why", "")):
            actors = _actors_for(candidates, data)
            reason = (f"the event you chose is not about the headline's main "
                      f"actor ({', '.join(actors)}) — it only shares a place "
                      f"with it; choose the origin of {actors[0]} itself")
            print(f"⚠️ Anchor rejected: not about the main actor "
                  f"({', '.join(actors)}). ({data.get('topic', '')[:70]})")
        elif not _anchor_is_relevant(data.get("subject", ""),
                                     data.get("topic", ""),
                                     data.get("why", "")):
            reason = ("the event you chose does not name anyone or anywhere "
                      "from the headline, so it explains nothing about it")
            print("⚠️ Anchor rejected: does not name any actor from the trending "
                  "subject, so it is background about the country rather than an "
                  "explanation of the story.")
        elif not _anchor_fits_niche(data.get("topic", ""), data.get("why", ""),
                                    story=story):
            reason = ("the event you chose is not a decision-and-consequence "
                      "story — name the choice someone made and the disaster it "
                      "caused, not a law, a treaty or a general overview")
            print(f"⚠️ Anchor rejected: not a catastrophic-decision story. "
                  f"({data.get('topic', '')[:70]})")

        if not reason:
            print(f"🧭 Trending subject: {data.get('subject', '?')}")
            print(f"   Anchored to {year}: {data['topic'][:100]}")
            return {"subject": data.get("subject", ""), "topic": data["topic"],
                    "name": (data.get("name") or "").strip(),
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


# Phrases that turn an open loop into a YouTube tease. Enforced only when
# CLICKBAIT_MODE=off — see llm_engine.py. With it on, a direct tease at the
# chapter seam is exactly the hard-sell language being tested.
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

    if not CLICKBAIT_MODE:
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


# ---------------------------------------------------------------------------
# The joke beat
#
# One deliberate break in tone, once per video, appended right after "The
# Decision" — the fatal call has just been made, and the dramatic-irony
# window ("you know what's coming") is exactly where a dry aside lands
# without reading as the channel laughing at what is about to happen.
# Generated as its own small call, separate from the Decision chapter
# itself, so its content and style tag are both controlled directly rather
# than trusted to a free model following one instruction buried in a longer
# brief.
#
# Only fires for formats that actually have a "The Decision" chapter — THE
# UNSOLVED substitutes it for "What Has Been Ruled Out", which has no
# decision-maker to roast, so it is skipped there by construction: the hook
# below checks the heading itself, not the format.
#
# The one hard rule — roast the decision, never the people it hurt — is
# enforced twice: in the prompt, and again in _joke_ok(), because a prompt
# rule a free model ignores once is not a rule.
# ---------------------------------------------------------------------------

JOKE_BEAT_MODE = os.getenv("JOKE_BEAT_MODE", "on").strip().lower() != "off"

# Casualty-adjacent language the joke must never touch — the one place in
# this pipeline where "did the model listen" is checked by regex rather
# than trusted.
_JOKE_FORBIDDEN = (
    r"\bdied\b", r"\bdeaths?\b", r"\bdead\b", r"\bkilled\b", r"\bdrowned\b",
    r"\bcrew\b", r"\bvictims?\b", r"\bcasualt(?:y|ies)\b", r"\bwounded\b",
    r"\bburn(?:ed|t)\b", r"\bsuffer(?:ed|ing)?\b", r"\bchildren\b",
    r"\bfamil(?:y|ies)\b", r"\bcrash(?:ed)?\b", r"\bexplo(?:ded|sion)\b",
)


def _joke_ok(text: str) -> bool:
    """Whether a joke-beat line stays clear of the one hard line: never the
    people it hurt, only the people who chose wrong."""
    if not text or len(text.split()) > 24:
        return False
    low = text.lower()
    return not any(re.search(p, low) for p in _JOKE_FORBIDDEN)


# Case-insensitive tells that the model wrote a real quote's framing instead
# of an original line.
_EPIGRAPH_FORBIDDEN = (
    r"\bsaid\b", r"\bwrote\b", r"\bonce said\b", r"\bquote(?:d)?\b",
    r"\battributed\b",
)

# Checked against the ORIGINAL text, not the lowercased copy: an em/en dash
# before a capitalised word is the exact shape of a "— Person" attribution
# line, and the capital is the only signal that makes it one.
_EPIGRAPH_ATTRIBUTION_DASH = re.compile(r"[—–]\s*[A-Z]")

MIN_EPIGRAPH_WORDS = 10
MAX_EPIGRAPH_WORDS = 22
MIN_KEYWORD_WORDS = 1
# Wide enough for a short but complete, quotable clause ("a man is a fool") —
# not just one or two words. What earns the isolated red hold is how
# unsettling or memorable the phrase is, not brevity for its own sake.
MAX_KEYWORD_WORDS = 6


def _epigraph_ok(text: str, keyword: str) -> bool:
    """Whether a generated intro-card line is safe to publish: an original
    editorial line, never dressed up as a real person's recorded words."""
    if not text or not keyword:
        return False
    n_words = len(text.split())
    if not (MIN_EPIGRAPH_WORDS <= n_words <= MAX_EPIGRAPH_WORDS):
        return False
    n_keyword = len(keyword.split())
    if not (MIN_KEYWORD_WORDS <= n_keyword <= MAX_KEYWORD_WORDS):
        return False
    if keyword.lower() not in text.lower():
        return False
    if _EPIGRAPH_ATTRIBUTION_DASH.search(text):
        return False
    low = text.lower()
    return not any(re.search(p, low) for p in _EPIGRAPH_FORBIDDEN)


def _norm_tokens(text: str):
    return [re.sub(r"[^a-z0-9]", "", w.lower()) for w in text.split()]


def _select_real_quote(pool, topic, candidates, tone, context_before,
                       context_after, era_year=None):
    """Asks the model to pick ONE of these real, sourced Wikiquote
    candidates for a black-screen title card and mark a keyword in it —
    never to write new words. Returns {"sentences", "keyword",
    "attribution", "attribution_title"} or None if nothing fit, the model
    picked nothing, or its answer failed the grounding check.

    tone describes the emotional register this particular card needs to
    hit (the opening card and the pre-verdict card want very different
    things from the same candidate pool); context_before/context_after
    are what the video actually says right before and after this card, so
    the pick is judged against the real moment it sits in rather than
    just the bare topic.

    The grounding check is the whole point of building this instead of
    just asking a model to "recall a quote": the model's job here is
    selection and light formatting (splitting into sentences, marking a
    phrase), and its output is verified word-for-word against the actual
    candidate text before anything downstream trusts it. A model that
    quietly reworded or embellished the quote fails this check exactly
    like one that invented it outright — the result is dropped, not
    corrected, because a "close enough" quote is still not what the real
    person said.
    """
    if not candidates:
        return None

    listing = "\n".join(
        f"{i + 1}. \"{c['text']}\" — {c['attribution']}"
        for i, c in enumerate(candidates))
    prompt = (
        f"Story: {topic}\n\n"
        f"What the video just said, right before this card appears:\n"
        f"...{context_before}\n\n"
        f"What the video says right after this card ends:\n"
        f"{context_after}...\n\n"
        f"Real, sourced quotes found for this story:\n{listing}\n\n"
        f"Pick the ONE that reads best RIGHT HERE, in this exact spot "
        f"between those two moments.\n"
        f"THE REGISTER IS REQUIRED, not a preference: the card must feel "
        f"{tone}. A quote that is only informative, rousing, defiant, "
        f"diplomatic or matter-of-fact (a declaration of readiness, a "
        f"policy statement, a battle order read aloud) does NOT qualify, "
        f"however relevant — answer 0 and an original line in the right "
        f"register is written instead. The same if none would make sense "
        f"read alone out of context.\n"
        f"THE QUOTE MUST BE ABOUT THIS STORY: its events, its people or "
        f"what was at stake in them. A line its speaker said on another "
        f"subject (a president's academic writing on public "
        f"administration, in a story about a jailed dissident) does NOT "
        f"qualify, even though the speaker is in the story — answer 0.\n"
        "HARD RULES:\n"
        "- Copy the chosen quote's wording EXACTLY, word for word — do "
        "not paraphrase, trim, modernise or correct it in any way. Split "
        "it into its individual sentences as a list, exactly as written.\n"
        "- Mark the single most striking phrase in it (1-6 words) — the "
        "words that should linger alone on screen, turned red, after the "
        "rest fades. Pick for impact, not brevity: the phrase that would "
        "leave someone thinking, unsettled, or a little afraid of what "
        "this person chose to say — not just the phrase closest to the "
        "topic. A short, complete, quotable clause (\"a man is a fool\") "
        "beats a single safe word. Mark it by wrapping it in double "
        "square brackets, e.g. \"...whose [[face is marred]] by dust and "
        "sweat.\" The brackets are removed before display, so the "
        "sentence must read naturally without them.\n"
        "- attribution_title: a brief role or era (e.g. \"Roman Emperor\", "
        "\"14th-century historian\") ONLY if the speaker's name alone "
        "would mean nothing to most viewers; otherwise an empty string.\n\n"
        'Output ONLY JSON: {"chosen": <number, or 0 if none fit>, '
        '"sentences": ["...with [[phrase]] marked..."], '
        '"attribution_title": "..."}'
    )
    try:
        data = _call(pool,
                     "You select and lightly format one real quote from a "
                     "given list. You never invent or alter quote text. "
                     "Output only JSON.",
                     prompt, max_tokens=600, expect={"chosen": int})
    except QuotaExhausted:
        raise
    if not data:
        return None

    chosen = data.get("chosen")
    if not isinstance(chosen, int) or not (1 <= chosen <= len(candidates)):
        return None
    candidate = candidates[chosen - 1]

    raw_sentences = data.get("sentences")
    if not isinstance(raw_sentences, list) or not raw_sentences:
        return None
    sentences, keyword = [], ""
    for s in raw_sentences:
        if not isinstance(s, str):
            return None
        matches = _PUNCH_RE.findall(s)
        if matches:
            keyword = matches[0].strip()
        sentences.append(_PUNCH_RE.sub(lambda m: m.group(1), s).strip())

    # The grounding check: the returned sentences, brackets stripped, must
    # be the EXACT same word sequence as the real candidate text — not
    # "close", not "equivalent", identical. Anything else means the model
    # altered the quote, and an altered quote is not what that person said.
    if _norm_tokens(" ".join(sentences)) != _norm_tokens(candidate["text"]):
        print(f"   ⚠️ Model's version of the chosen quote didn't match the "
              f"sourced text verbatim — discarding rather than trust it.")
        return None
    joined = " ".join(sentences)
    if (not keyword or keyword.lower() not in joined.lower()
            or not (MIN_KEYWORD_WORDS <= len(keyword.split()) <= MAX_KEYWORD_WORDS)):
        # A missing or oversized keyword costs the exit its isolated-word
        # moment, not the whole quote — the renderer already skips that
        # phase cleanly when no keyword is given.
        keyword = ""

    return {
        "sentences": sentences,
        "keyword": keyword,
        "attribution": candidate["attribution"],
        "attribution_title": str(data.get("attribution_title") or "").strip(),
    }


def _extract_search_terms(pool, topic):
    """A few short, clean search phrases pulled from the topic — a
    person's name, a place, an event title. Wikiquote's search does much
    worse on a full descriptive sentence (the topic strings this pipeline
    generates run 30-40 words, e.g. "Woodrow Wilson's signing of the 1917
    Espionage Act and the aggressive prosecution of anti-war publisher
    Charles Schenck, which established...") than on the name it's
    actually about, and the punctuation inside a sentence-length query
    (apostrophes, embedded quote marks) can confuse a wiki search outright.
    Falls back to the proper names in the topic on any failure (see
    _names_in), and only to the raw topic when it has none. Searching the
    raw sentence finds nothing: that is exactly how the 2026-09-25 Kinmen
    video lost both title cards, after a 150-token ceiling cut off two
    reasoning models' replies to this call.
    """
    fallback = _names_in(topic) or [topic]
    prompt = (
        f"Topic: {topic}\n\n"
        "List up to 3 short search phrases from this topic that would "
        "make good queries on a quotations website — a named person "
        "first if the topic has one, then a specific place or event "
        "name. Each phrase should be 1-4 words, exactly as it would "
        "appear as a real name or title — no extra description, no "
        "surrounding sentence.\n"
        'Output ONLY JSON: {"queries": ["...", "..."]}'
    )
    try:
        data = _call(pool, "You extract short, clean search phrases from "
                     "a topic sentence. Output only JSON.",
                     prompt, max_tokens=1000, expect={"queries": list})
    except QuotaExhausted:
        raise
    except Exception as e:
        print(f"⚠️ Search-term extraction failed ({type(e).__name__}: {e}) "
              f"— searching {fallback!r} instead.")
        return fallback

    # A weak model can echo the template back: the Six-Day War run searched
    # Wikiquote for the literal "..." twice, found nothing, and both cards
    # fell back to invented lines. A query needs real letters.
    queries = [q.strip() for q in (data or {}).get("queries") or []
               if isinstance(q, str) and len(re.findall(r"[A-Za-z]", q)) >= 3
               and "..." not in q]
    return queries[:3] or fallback


def _names_in(topic, limit=3):
    """Proper names in a topic sentence, longest first: runs of capitalised
    words ("Mao Zedong", "Second Taiwan Strait Crisis", "Kinmen"), with a
    sentence-initial "The" dropped. No model involved, so it still works
    when every model is truncating or out of quota."""
    runs = re.findall(r"[A-Z][\w'-]*(?:\s+(?:of\s+|the\s+)?[A-Z][\w'-]*)*",
                      topic or "")
    names, seen = [], set()
    for r in runs:
        r = re.sub(r"^(The|A|An)\s+", "", r).strip()
        r = re.sub(r"'s$", "", r)
        if len(r) < 4 or r.lower() in seen or r in ("The", "A", "An"):
            continue
        seen.add(r.lower())
        names.append(r)
    names.sort(key=lambda n: -len(n.split()))
    return names[:limit]


_NOT_A_NAME = {"article", "quoted", "quote", "by", "describing", "about", "said",
               "radio", "broadcast", "television", "tv", "newspaper", "agency",
               "wrote", "according", "the", "unknown", "anonymous", "source",
               "report", "reports", "newspaper", "editorial", "project"}
_NAME_PARTICLES = {"al", "el", "bin", "ibn", "de", "da", "di", "van", "von", "der", "la", "le", "of", "abu", "ben"}


def _speaker_ok(name):
    """A card's speaker must be a person's name ("Gamal Abdel Nasser",
    "Hussein of Jordan"), not a description of a source. The Six-Day War
    run printed "Quoted by Project Herzl." and "The article describing
    Yitzhak Rabin" under its two cards."""
    if re.search(r"\d", name or ""):
        return False      # "Cairo Radio, May 25, 1967" is a broadcast, not a person
    words = re.findall(r"[A-Za-z][A-Za-z'.]*", name or "")
    if not (1 <= len(words) <= 6):
        return False
    if any(w.lower().strip(".") in _NOT_A_NAME for w in words):
        return False
    return all(w[0].isupper() or w.lower() in _NAME_PARTICLES for w in words)


def _quote_ok(text):
    """A whole spoken line: starts like a sentence and ends like one, not a
    fragment lifted from the middle of an article sentence ("chose the
    least ostentatious, the Six-Day War...")."""
    t = (text or "").strip().strip('"“”')
    # an edited quote ("Millions of Arabs are … preparing") is not what was said
    if "…" in t or "..." in t:
        return False
    return bool(t) and (t[0].isupper() or t[0].isdigit()) and t[-1] in ".!?"


def _gather_quote_candidates(pool, topic, sheet_quotes=None, cast=None):
    """Every real, sourced quote candidate Wikiquote has for this topic —
    searched once and shared by every card this video needs (the opening
    card and the pre-verdict card draw from the same pool, since they're
    about the same story), rather than re-querying per card."""
    try:
        search_terms = _extract_search_terms(pool, topic)
    except QuotaExhausted:
        raise
    # Story mode: every cast member is searched too, so each side of the
    # story has a chance to speak on the cards, not only whoever the topic
    # sentence happens to name.
    for c in cast or []:
        name = (c.get("name") or "").strip()
        if name and not any(_speaker_key(name) == _speaker_key(t) for t in search_terms):
            search_terms.append(name)

    candidates, seen = [], set()
    # Story mode: the fact sheet's quotes come first. Each one is verbatim
    # in the event's Wikipedia article (verify_sheet checks it) and carries
    # its speaker, so none can be invented.
    for q in sheet_quotes or []:
        text, who = (q.get("text") or "").strip(), (q.get("speaker") or "").strip()
        if (text and _speaker_ok(who) and _quote_ok(text)
                and 4 <= len(text.split()) <= 40 and text.lower() not in seen):
            seen.add(text.lower())
            candidates.append({"text": text, "attribution": who,
                               "source_page": "Wikipedia"})
    for term in search_terms:
        try:
            found = search_quotes(term)
        except Exception as e:
            print(f"⚠️ Quote search failed for {term!r} "
                  f"({type(e).__name__}: {e}).")
            continue
        for c in found:
            key = c["text"].lower()
            if not (_speaker_ok(c.get("attribution")) and _quote_ok(c.get("text"))):
                continue
            if key not in seen:
                seen.add(key)
                candidates.append(c)

    if candidates:
        print(f"🔍 {len(candidates)} sourced quote candidate(s) found for "
              f"{topic!r} (searched {search_terms!r}).")
    else:
        print(f"🔍 No sourced quote candidates found (searched "
              f"{search_terms!r}).")
    return candidates


_TITLE_WORDS = {"king", "queen", "president", "prime", "minister", "general",
                "field", "marshal", "chief", "staff", "defense", "defence",
                "foreign", "military", "army", "the", "of", "and", "leader",
                "commander", "premier", "emperor", "chancellor", "secretary",
                "state", "admiral", "colonel", "sir", "lord", "pope", "shah",
                "jr", "sr", "ii", "iii"}


def _cast_member(attribution, cast):
    """The cast member a quote's speaker is, matched on a distinctive word of
    the name ("Hussein of Jordan" is King Hussein), or None."""
    words = set(re.findall(r"[a-z'-]+", (attribution or "").lower()))
    for c in cast or []:
        own = [w for w in re.findall(r"[a-z'-]+", c.get("name", "").lower())
               if len(w) > 3 and w not in _TITLE_WORDS]
        if own and own[-1] in words:
            return c
    return None


def _side_of(member):
    """The country or camp a cast member stands for, read from the first
    proper name in their role ("Egypt's president" -> "egypt", "King of
    Jordan" -> "jordan"); "" when the role names none."""
    for w in re.findall(r"[A-Z][A-Za-z-]+", (member or {}).get("role") or ""):
        if w.lower() not in _TITLE_WORDS:
            return w.lower()
    return ""


def _speaker_key(attribution, cast=None):
    """One key per person, so "Gamal Abdel Nasser" and "Nasser" count as the
    same speaker."""
    member = _cast_member(attribution, cast)
    if member:
        return member["name"].lower()
    words = [w for w in re.findall(r"[a-z'-]+", (attribution or "").lower())
             if len(w) > 2 and w not in _TITLE_WORDS]
    return words[-1] if words else ""


def _default_keyword(text):
    """The red phrase when no model marked one: the last clause of the quote
    (after its last comma, semicolon or dash), cut to its final six words.
    Always words the person really said, so the card always ends on red."""
    last = re.split(r"(?<=[.!?])\s+", text.strip())[-1]
    clause = re.split(r"[,;:\u2014\u2013]|\s-\s", last)[-1]
    words = clause.strip().rstrip(".!?\"'\u201d").split()
    if len(words) < 3:
        # too short to carry the card ("were brilliant"): the sentence's
        # last five words instead, commas and all, still exactly as said
        words = last.strip().rstrip(".!?\"'\u201d").split()[-5:]
    if len(words) > MAX_KEYWORD_WORDS:
        words = words[-MAX_KEYWORD_WORDS:]
    while words and words[0].lower() in ("and", "but", "or", "that", "which", "so"):
        words = words[1:]
    return " ".join(words) if len(words) >= 2 else ""


def _source_intro_quote(pool, topic, tone, context_before, context_after,
                        candidates, era_year=None, exclude_texts=None,
                        exclude_speakers=None, real_only=False, cast=None,
                        exclude_sides=None):
    """One black-screen title card's content: a real, sourced quote from
    `candidates` when one actually fits this spot, an original
    unattributed line (see _generate_intro_epigraph) otherwise. Search
    first, not the model's own memory — see quote_search.py for why.

    candidates comes from _gather_quote_candidates, shared across every
    card in this video; exclude_texts and exclude_speakers keep the second
    card from repeating the first card's quote or its speaker — the owner
    wants two different voices, not one voice twice. Compared with case and
    spacing normalised, since a used quote comes back split into sentences.
    """
    def _key(t):
        return " ".join(re.findall(r"[a-z0-9']+", (t or "").lower()))
    texts = {_key(t) for t in (exclude_texts or ())}
    speakers = {_speaker_key(a, cast) for a in (exclude_speakers or ()) if a}
    usable = [c for c in candidates
              if _key(c["text"]) not in texts
              and _speaker_key(c.get("attribution"), cast) not in speakers]
    # Story mode: the cast speaks first, and the second card goes to the
    # other side of the story (docs/VIDEO_SPEC.md 11). Each tier is used
    # only when it has quotes; otherwise the next wider one.
    if cast and usable:
        sides = {s for s in (exclude_sides or ()) if s}
        in_cast = [c for c in usable if _cast_member(c.get("attribution"), cast)]
        other_side = [c for c in in_cast
                      if _side_of(_cast_member(c.get("attribution"), cast)) not in sides]
        usable = other_side or in_cast or usable

    if usable:
        try:
            selected = _select_real_quote(pool, topic, usable, tone,
                                          context_before, context_after,
                                          era_year=era_year)
        except QuotaExhausted:
            raise
        if selected:
            if not selected.get("keyword"):
                selected["keyword"] = _default_keyword(" ".join(selected["sentences"]))
            preview = " ".join(selected["sentences"])[:70]
            print(f"   📜 Using a real quote — {selected['attribution']}: "
                  f"{preview!r}")
            return selected
        if real_only:
            # Story mode: the owner wants real words on these cards, never
            # an invented line. When no quote hits the register, the most
            # card-like real one is used as written: a whole short quote,
            # 8-40 words, from a named speaker.
            pick = sorted((c for c in usable if 8 <= len(c["text"].split()) <= 40),
                          key=lambda c: abs(len(c["text"].split()) - 22))
            if pick:
                c = pick[0]
                print(f"   📜 No quote hit the register — using the closest "
                      f"real one as written — {c['attribution']}: {c['text'][:70]!r}")
                return {"sentences": [c["text"]],
                        "keyword": _default_keyword(c["text"]),
                        "attribution": c["attribution"], "attribution_title": ""}
        print("   ↩️  None of the sourced quotes fit — falling back to an "
              "original line.")

    if real_only:
        return None
    return _generate_intro_epigraph(pool, topic, tone, context_before,
                                    era_year=era_year)


def _generate_intro_epigraph(pool, topic, tone, context_before, era_year=None):
    """One original, unattributed line for a black-screen title card — the
    Far Cry 6 style cold open: a poetic line in the register `tone` asks
    for, with one short phrase marked to linger alone in red after the
    rest fades.

    This is the fallback when quote_search finds nothing usable (see
    _source_intro_quote, which every caller should use instead of this
    directly). Deliberately never a real historical quote itself: a free
    model asked to "recall" one will confidently invent a plausible-
    sounding fake and misattribute it to a real person, and this pipeline
    publishes without anyone reading the script first. An original line
    carries the same weight without ever claiming to be someone's real
    recorded words. Returns None if nothing clean was written within the
    attempt budget — losing this card costs one intro, not the video.
    """
    prompt = (
        f"Story: {topic}\n\n"
        f"What the video just said, right before this card appears:\n"
        f"...{context_before}\n\n"
        f"Write ONE original, unattributed line for a black-screen title "
        f"card at this exact point in the video — {tone}. Not a summary "
        f"of what happened and not a specific fact, name, date or number "
        f"from the story.\n"
        "HARD RULES:\n"
        "- This is NOT a real quote and must never be dressed up as one: "
        "no attribution, no name, no dash-and-name, no \"as they said\", "
        "nothing implying a real historical figure spoke these words. It "
        "is an original editorial line, full stop.\n"
        "- 12 to 20 words, one sentence, no quotation marks around it.\n"
        "- Mark the single most striking phrase in it (1-6 words) — the "
        "words that should linger alone on screen, turned red, after the "
        "rest fades. Pick for impact, not brevity: the phrase that would "
        "leave someone thinking, unsettled, or a little afraid — a short, "
        "complete clause beats one safe word. Mark it by wrapping it in "
        "double square brackets, e.g. \"Power forgets [[the warning]] it "
        "was given.\" The brackets are removed before display, so the "
        "sentence must read naturally without them.\n\n"
        'Output ONLY JSON with one key, "text", holding your line.'
    )
    # Each retry goes to a different model. On 2026-09-25 the run had fallen
    # through to one weak free model that echoed the old output template
    # ("...with [[phrase]] marked...") back verbatim on all four attempts,
    # because every retry was handed straight back to the same pinned model.
    tried = set()
    for attempt in range(3):
        data = _call(pool,
                     "You write one original, unattributed epigraph line "
                     "for a title card. Never a real quote. Output only "
                     "JSON.",
                     prompt, max_tokens=1000, skip=tried, expect={"text": str})
        if _last_model:
            tried.add(_last_model)
        raw = (data or {}).get("text", "").strip()
        matches = _PUNCH_RE.findall(raw)
        keyword = matches[0].strip() if matches else ""
        text = _PUNCH_RE.sub(lambda m: m.group(1), raw).strip()
        if _epigraph_ok(text, keyword):
            return {"text": text, "keyword": keyword}
        print(f"   ⚠️ Intro card rejected (attempt {attempt + 1}/3, "
              f"{_last_model})"
              f"{': ' + raw[:60] if raw else ''}")
    print("   ↩️  No usable written line — the stock bank takes over.")
    return None


# The last layer under both title cards, so a card is never skipped: when
# no sourced quote fits AND no model writes a usable line (every provider out
# of quota, or the only model left echoing the prompt back, as on the
# 2026-09-25 Kinmen run), one of these is used. Original lines, written for
# this channel, never attributed to anyone. Generic on purpose: no names,
# dates or facts, so none can be wrong about any story. [[...]] marks the
# phrase that lingers alone in red.
STOCK_OPENING_LINES = (
    "Every catastrophe begins as a quiet decision that [[nobody thought to question]].",
    "The men who signed the order slept well that night. [[The dead did not]].",
    "History does not forget the warning. It only remembers [[who chose to ignore it]].",
    "It was never the storm that sank them. It was [[the certainty of the captain]].",
    "Somewhere behind every disaster is a room, a signature, and [[a silence that agreed]].",
    "The mistake was small enough to fit on one page, and [[large enough to bury thousands]].",
    "They were not fools. They were sure, and [[certainty is far more dangerous]].",
    "The warning arrived on time. It was simply filed [[where no one would look]].",
    "Power rarely falls to its enemies. It falls to [[its own unexamined confidence]].",
    "Nobody who made this choice believed they were writing [[the first line of a tragedy]].",
)
STOCK_VERDICT_LINES = (
    "The bill always comes due, and it is never paid [[by the ones who ordered it]].",
    "What was lost could not be counted, only [[carried by those who were left]].",
    "Afterwards everyone remembered the warning. By then [[remembering was all that was left]].",
    "No inquiry, no apology, no monument can [[undo a single hour of it]].",
    "The decision took an afternoon. The consequences [[outlived everyone who made it]].",
    "In the end the record is clear, and [[clarity came far too late]].",
    "The ones who paid were never asked, and [[their silence is the verdict]].",
    "It was not fate. It was a choice, and [[choices leave their names behind]].",
    "Time buries the ruins, but it never quite [[buries the question of why]].",
    "They called it an accident, because the truth [[had too many signatures on it]].",
)


def stock_epigraph(topic, verdict=False, exclude_texts=None):
    """A title card that needs no model at all. Picked by the topic's hash so
    each video gets a stable line and different videos get different ones;
    skips anything in exclude_texts so the two cards never repeat."""
    import hashlib
    bank = STOCK_VERDICT_LINES if verdict else STOCK_OPENING_LINES
    exclude = {t.lower() for t in (exclude_texts or ())}
    # Two cards leaning on the same word ("the warning" ... "the warning")
    # read as one idea said twice, so a line sharing any key word (5+
    # letters) with a card already used is passed over.
    used_words = {w for t in exclude for w in re.findall(r"[a-z]{5,}", t)}
    start = int(hashlib.sha1((topic or "").encode()).hexdigest(), 16) % len(bank)
    for i in range(len(bank)):
        raw = bank[(start + i) % len(bank)]
        text = _PUNCH_RE.sub(lambda m: m.group(1), raw)
        if text.lower() in exclude:
            continue
        if used_words & set(re.findall(r"[a-z]{5,}", text.lower())):
            continue
        return {"text": text, "keyword": _PUNCH_RE.findall(raw)[0]}
    for i in range(len(bank)):
        raw = bank[(start + i) % len(bank)]
        text = _PUNCH_RE.sub(lambda m: m.group(1), raw)
        if text.lower() not in exclude:
            return {"text": text, "keyword": _PUNCH_RE.findall(raw)[0]}
    raw = bank[start]
    return {"text": _PUNCH_RE.sub(lambda m: m.group(1), raw),
            "keyword": _PUNCH_RE.findall(raw)[0]}


def _generate_joke_beat(pool, topic, decision_text, era_year=None):
    """One dry, ironic aside about the decision just made. Returns a segment
    dict ready to append to a chapter's scenes, or None if it could not be
    written cleanly within the attempt budget — losing this beat costs one
    joke, not the video."""
    if not JOKE_BEAT_MODE:
        return None

    prompt = (
        f"Story: {topic}\n\n"
        f"The video just narrated this decision:\n{decision_text[-700:]}\n\n"
        "Write ONE short, dry, ironic sentence about the hubris, "
        "overconfidence or bureaucratic absurdity of the people who chose "
        "wrong — a single beat of dark humor. This is the ONE moment in the "
        "whole video allowed to be funny; everywhere else stays factual and "
        "forensic.\n"
        "HARD RULES:\n"
        "- Target ONLY the decision-makers' judgment or process — their "
        "confidence, their paperwork, their meetings, their assumptions. "
        "NEVER the people who were hurt by the decision, never death, injury "
        "or loss in any form.\n"
        "- It must be a TRUE, specific irony from THIS story — not an "
        "invented joke, and not a generic one that would fit any disaster.\n"
        "- One sentence, under 20 words. Dry and deadpan, not a pun, not a "
        "comedian's voice.\n\n"
        "Also write image_prompt: one clear focal subject, shot type first, "
        "in the channel's usual style.\n"
        'Output ONLY JSON: {"narration": "...", "image_prompt": "..."}'
    )
    for attempt in range(2):
        data = _call(pool, "You write one dry, ironic aside. Output only JSON.",
                    prompt, max_tokens=800)
        narration = (data or {}).get("narration", "").strip()
        image_prompt = (data or {}).get("image_prompt", "").strip()
        if narration and image_prompt and is_speakable(narration) \
                and _joke_ok(narration):
            return {
                "narration": narration,
                "image_prompt": build_generation_prompt(image_prompt,
                                                         era_year=era_year),
                "archive_query": None,
                "tts_style": "funny",
            }
        print(f"   ⚠️ Joke beat rejected (attempt {attempt + 1}/2)"
              f"{': ' + narration[:60] if narration else ''}")
    print("   ↩️  Skipping the joke beat this run.")
    return None


def _simplify_chapter(pool, story, data, heading, words, said_lines,
                      said_sentences, enforce_history, rewrite, is_open=False,
                      c_index=None, earlier_text="", era_year=None):
    """Story mode's simplicity gate. A counting check (names outside the
    cast, places outside the list, unit names, figures, long sentences) and
    a model reading as a viewer with no background; if either finds
    something, ONE rewrite with those exact points. The rewrite is kept only
    if it is a sound chapter that is no harder than the first; out of quota,
    the chapter stays as it was."""
    from story_mode import simplicity_issues, viewer_check, structure_issues
    plan, sheet = story["plan"], story["sheet"]
    text = " ".join(x["narration"] for x in data["segments"])
    issues = simplicity_issues(text, plan, sheet)
    # the agreed structure (docs/VIDEO_SPEC.md 1-3): teaser, cast chapter,
    # a spoken bridge when a chapter goes back in time
    shape = (structure_issues(c_index, plan, data["segments"], earlier_text, era_year, sheet)
             if c_index is not None else [])
    for pt in shape:
        print(f"   📐 Structure: {pt[:150]}")
    try:
        confusing = viewer_check(pool, text, " ".join(said_lines), _call)
    except QuotaExhausted:
        print("   🛑 Quota gone — simplicity check skipped, chapter kept.")
        return data
    if not issues and not confusing and not shape:
        print("   🧒 Simplicity check: clear to a viewer with no background.")
        return data
    points = shape + issues + [f"A viewer would be lost here: {c}" for c in confusing]
    print(f"   🧒 Simplicity check flagged {len(points)} point(s):")
    for pt in points:
        print(f"      - {pt[:140]}")
    feedback = (
        "Your previous attempt needs changes. Write the chapter again, telling "
        "the same events, for a viewer with no historical or political "
        "background, and fix exactly these points:\n"
        + "\n".join(f"- {pt}" for pt in points)
        + "\n\nYOUR PREVIOUS ATTEMPT:\n" + text)
    try:
        candidate = rewrite(feedback)
    except QuotaExhausted:
        print("   🛑 Quota gone — keeping the chapter as written.")
        return data
    if not _validate_chapter(candidate, words, enforce_history=enforce_history,
                             previous_sentences=said_sentences):
        print("   ↩️  Rewrite unusable — keeping the first version.")
        return data
    if is_open and _opening_hook_ok(data) and not _opening_hook_ok(candidate):
        print("   ↩️  Rewrite lost the opening hook — keeping the first version.")
        return data
    new_text = " ".join(x["narration"] for x in candidate["segments"])
    left = simplicity_issues(new_text, plan, sheet)
    shape_left = (structure_issues(c_index, plan, candidate["segments"], earlier_text, era_year, sheet)
                  if c_index is not None else [])
    # structure first: a rewrite that fixes the structure is kept even if it
    # is no simpler; one that breaks it is never kept
    if len(shape_left) > len(shape) or (len(shape_left) == len(shape) and len(left) > len(issues)):
        print("   ↩️  Rewrite was no better — keeping the first version.")
        return data
    for pt in shape_left:
        print(f"   ⚠️ Structure still off: {pt[:150]}")
    print(f"   ✅ Rewritten: structure {len(shape)} -> {len(shape_left)}, "
          f"simplicity {len(issues)} -> {len(left)}.")
    return candidate


def generate_longform_script(topic: str = None, enforce_history: bool = False,
                             era_year: int = None, story=None) -> dict:
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
    # Story mode (story_mode.py): the arc, the roles and the hooks come from
    # the researched outline, and the writer works under the story voice.
    story_roles = story_hooks = None
    story_segments = []
    story_text = ""
    system_prompt = SYSTEM_PROMPT
    if story:
        from story_mode import story_arc, STORY_SYSTEM_PROMPT
        arc, story_roles, story_hooks = story_arc(story["plan"], story["sheet"])
        from story_mode import segment_plans
        story_segments = segment_plans(story["plan"])
        system_prompt = STORY_SYSTEM_PROMPT.format(style=STYLE_SUFFIX)
        print(f"📖 Story mode: {len(arc)} chapters, "
              f"~{sum(w for _, _, w in arc)} words — {story['plan']['question']}")
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
        if story_roles:
            pacing = next((p for p in PACING if p["role"] == story_roles[c_index]),
                          pacing)
        # The last chapter closes the video; every other one hands off.
        next_heading = arc[c_index + 1][0] if c_index + 1 < len(arc) else None
        seg_plan = story_segments[c_index] if c_index < len(story_segments) else None
        seg_rule = seg_plan[1] if seg_plan else None
        print(f"✍️  Chapter: {heading} (~{words} words, {pacing['role']}, "
              f"cut every ~{pacing['words_per_image']} words)...")
        cached = run_cache.chapter(topic, c_index, heading) if story else None
        if cached:
            # written by the last run of this lane before it died (run_cache.py)
            chapters.append(cached)
            chapter_text = " ".join(x["narration"] for x in cached["scenes"])
            said_sentences.extend(_sentences(chapter_text))
            said_lines.extend(
                line.strip() for line in re.split(r"(?<=[.!?])\s+", chapter_text)
                if len(line.split()) >= 4)
            story_text += " " + chapter_text
            tail = cached["scenes"][-1]["narration"][-160:]
            print(f"   ♻️  Already written by the last run — reused "
                  f"({len(chapter_text.split())} words, no request spent).")
            continue
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
                candidate = _call(pool, system_prompt,
                                  _chapter_prompt(topic, heading, brief, words,
                                                  tail, feedback,
                                                  era_year=era_year,
                                                  covered=said_lines,
                                                  episode_format=episode_format,
                                                  next_heading=next_heading,
                                                  words_per_image=pacing["words_per_image"],
                                                  story_hook=(story_hooks[c_index]
                                                              if story_hooks else None),
                                                  segment_rule=seg_rule),
                                  max_tokens=CHAPTER_MAX_TOKENS,
                                  expect=_chapter_reply_problem)
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
                        and (not story_hooks or story_hooks[c_index])
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
            if story and got < words * 0.5:
                # too thin to publish: the halves below get one more chance
                print(f"   ↩️  Best attempt at {heading!r} is {got} of ~{words} words.")
                data = None
            else:
                print(f"   ⚠️ Accepting the best attempt at {heading!r} ({got} "
                      f"words) rather than losing the run.")

        if not data and story and words >= SPLIT_MIN_WORDS:
            # The free fallbacks cut a long chapter off mid-reply (13 Oct,
            # "Armies on Five Fronts", 550 words, twice): two halves each fit.
            print(f"   ✂️  Writing {heading!r} in two halves...")
            prompt_for = lambda w, t, fb, cov: _chapter_prompt(
                topic, heading, brief, w, t, fb, era_year=era_year,
                covered=cov, episode_format=episode_format,
                next_heading=next_heading,
                words_per_image=pacing["words_per_image"],
                story_hook=story_hooks[c_index] if story_hooks else None,
                segment_rule=seg_rule)
            data = _chapter_in_halves(pool, system_prompt, prompt_for, words,
                                      tail, said_lines, enforce_history)

        if not data:
            print(f"❌ Chapter {heading!r} could not be generated.")
            if story:
                # A story cannot lose a chapter: the 2026-09-28 Vajont run lost
                # its turning point, climax and aftermath to spent quotas and
                # still uploaded a 4-minute video that stopped mid-story.
                print("❌ Story mode: a chapter is missing — stopping rather "
                      "than publishing half a story.")
                return None
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

        if story:
            data = _simplify_chapter(
                pool, story, data, heading, words, said_lines, said_sentences,
                enforce_history,
                lambda fb: _call(pool, system_prompt, _chapter_prompt(
                    topic, heading, brief, words, tail, fb, era_year=era_year,
                    covered=said_lines, episode_format=episode_format,
                    next_heading=next_heading,
                    words_per_image=pacing["words_per_image"],
                    story_hook=story_hooks[c_index] if story_hooks else None,
                    segment_rule=seg_rule),
                    max_tokens=CHAPTER_MAX_TOKENS, expect=_chapter_reply_problem),
                is_open=heading == opening_heading,
                c_index=c_index, earlier_text=story_text, era_year=era_year)
            story_text += " " + " ".join(x["narration"] for x in data["segments"])

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

        # Strips [[ ]] markers from segs in place — must run before
        # chapter_text below, so continuity tracking and the joke-beat
        # prompt never see stray brackets in "already narrated" text.
        punch_phrase, punch_idx = _extract_punch_phrase(segs)

        chapter_text = " ".join(x["narration"] for x in segs)
        said_sentences.extend(_sentences(chapter_text))
        said_lines.extend(
            line.strip() for line in re.split(r"(?<=[.!?])\s+", chapter_text)
            if len(line.split()) >= 4)

        # arc_index, not the position in this list: a chapter that failed to
        # generate is dropped, and everything downstream that cares about the
        # midpoint would otherwise silently shift to the wrong chapter.
        chapters.append({"heading": heading, "scenes": segs,
                         "punch_phrase": punch_phrase, "punch_idx": punch_idx,
                         "arc_index": c_index, "pacing": pacing})
        tail = segs[-1]["narration"][-160:]
        total = sum(len(x["narration"].split()) for x in segs)
        print(f"   ✅ {len(segs)} segments, {total} words")
        if story:
            run_cache.save_chapter(topic, c_index, chapters[-1])

        # The one joke, right after the fatal call — see _generate_joke_beat.
        # Appended to segs directly (not folded into chapter_text/said_lines
        # above) so it never counts as narrated fact a later chapter must
        # avoid repeating, and never becomes the "previous chapter ended
        # with" continuity anchor for THE CHAIN.
        # The old arc's one joke; never in story mode, where a pivot chapter
        # may also be titled "The Decision" (the sixth script got one).
        if heading == "The Decision" and not story:
            joke = _generate_joke_beat(pool, topic, chapter_text, era_year=era_year)
            if joke:
                segs.append(joke)
                print(f"   🎭 Joke beat: {joke['narration'][:70]!r}")

    if len(chapters) < MIN_PUBLISHABLE_CHAPTERS:
        print(f"❌ Only {len(chapters)} of {len(arc)} chapters survived — below "
              f"the {MIN_PUBLISHABLE_CHAPTERS} needed for a publishable arc.")
        return None
    if dropped:
        print(f"⚠️ Publishing without {len(dropped)} chapter(s): "
              f"{', '.join(dropped)}.")

    # Two black-screen title cards, sourced from the same real-quote search
    # when Wikiquote has something that fits: one right after the opening
    # chapter, one right before the closing chapter — a sad, weary beat
    # that resets the viewer's attention before the verdict lands. Built
    # only now, once every chapter's real text exists, so each card is
    # judged (or written) against what the video actually says immediately
    # before and after it, not just the bare topic string. All six
    # chapters are already paid for by this point, so a quota wall here
    # costs the run its cards, never the video already in hand.
    intro_epigraph = verdict_epigraph = None
    real_only = False
    try:
        def _chapter_text(chapter):
            return " ".join(s["narration"] for s in chapter["scenes"])

        story_cast = (story or {}).get("plan", {}).get("cast") or []
        quote_candidates = _gather_quote_candidates(
            pool, topic, sheet_quotes=(story or {}).get("sheet", {}).get("quotes")
            if story else None, cast=story_cast)
        if story:
            # Only people who belong to this story may speak on its cards:
            # a cast member, or someone the fact sheet names. The Vajont run
            # opened on Martin Luther King Jr and a line of 18th-century
            # love poetry, because the search had found them.
            sheet_words = set(re.findall(r"[a-z'-]+", json.dumps(
                story.get("sheet") or {}, ensure_ascii=False).lower()))
            def _belongs(c):
                if _cast_member(c.get("attribution"), story_cast):
                    return True
                key = _speaker_key(c.get("attribution"))
                return bool(key) and key in sheet_words
            kept = [c for c in quote_candidates if _belongs(c)]
            if len(kept) < len(quote_candidates):
                print(f"   ↩️  {len(quote_candidates) - len(kept)} quote(s) dropped: "
                      f"speaker not in this story.")
            quote_candidates = kept
        real_only = bool(story)

        # The owner's brief for the two cards: both dark, mysterious and
        # disturbing; the second one sad as well, because by then the story
        # has a lesson in it.
        opening_tone = (
            "dark, mysterious, disturbing and concerning — an omen: it "
            "unsettles the viewer about what is coming, the KIND of failure "
            "this story is, without explaining it"
        )
        intro_epigraph = _source_intro_quote(
            pool, topic, opening_tone,
            context_before=_chapter_text(chapters[0])[-700:],
            context_after=_chapter_text(chapters[1])[:500],
            candidates=quote_candidates, era_year=era_year, real_only=real_only,
            cast=story_cast)
        if intro_epigraph:
            preview = " ".join(intro_epigraph.get("sentences")
                               or [intro_epigraph.get("text") or ""])
            print(f"🖤 Opening card: {preview[:70]!r}")

        if len(chapters) >= 3:
            used = set()
            if intro_epigraph:
                used.add(" ".join(intro_epigraph.get("sentences")
                                  or [intro_epigraph.get("text") or ""]))
            verdict_tone = (
                "dark, mysterious and disturbing like the opening card, but "
                "SAD as well — it carries the lesson: the cost has already "
                "been paid and nothing reverses it now; a quiet gut-punch "
                "right before the verdict, not a cliffhanger"
            )
            verdict_epigraph = _source_intro_quote(
                pool, topic, verdict_tone,
                context_before=_chapter_text(chapters[-2])[-700:],
                context_after=_chapter_text(chapters[-1])[:500],
                candidates=quote_candidates, era_year=era_year,
                exclude_texts=used,
                exclude_speakers={(intro_epigraph or {}).get("attribution")},
                exclude_sides={_side_of(_cast_member(
                    (intro_epigraph or {}).get("attribution"), story_cast))},
                real_only=real_only, cast=story_cast)
            if verdict_epigraph:
                preview = " ".join(verdict_epigraph.get("sentences")
                                   or [verdict_epigraph.get("text") or ""])
                print(f"🖤 Pre-verdict card: {preview[:70]!r}")
    except QuotaExhausted:
        print("🛑 Quota gone before the title cards — using the channel's "
              "own stock lines for them.")
    except Exception as e:
        print(f"⚠️ Title-card selection failed ({type(e).__name__}: {e}) — "
              f"using the channel's own stock lines.")

    # Never skip a card: whatever the layers above could not produce comes
    # from the stock bank, which needs no model and cannot fail. In story
    # mode a card with no real quote left gets an original line first
    # (owner, 2026-09-29: better than no card), written for this moment and
    # never attributed to anyone, so it can never pass for someone's words.
    if real_only:
        def _original(tone, before, verdict=False, exclude=()):
            try:
                got = _generate_intro_epigraph(pool, topic, tone, before,
                                               era_year=era_year)
            except Exception as e:
                print(f"   ↩️  Original line failed ({type(e).__name__}).")
                got = None
            if got and got.get("text") not in set(exclude):
                return got
            return stock_epigraph(topic, verdict=verdict,
                                  exclude_texts=set(exclude))

        def _text(e):
            return " ".join((e or {}).get("sentences") or [(e or {}).get("text") or ""])
        if not intro_epigraph:
            intro_epigraph = _original(
                "dark, mysterious, disturbing and concerning — an omen",
                " ".join(s["narration"] for s in chapters[0]["scenes"])[-700:])
            print(f"🖤 Opening card (original line, unattributed): "
                  f"{_text(intro_epigraph)[:70]!r}")
        if len(chapters) >= 3 and not verdict_epigraph:
            verdict_epigraph = _original(
                "dark and sad — the cost has already been paid and nothing "
                "reverses it now; a quiet gut-punch",
                " ".join(s["narration"] for s in chapters[-2]["scenes"])[-700:],
                verdict=True, exclude={_text(intro_epigraph)})
            print(f"🖤 Pre-verdict card (original line, unattributed): "
                  f"{_text(verdict_epigraph)[:70]!r}")
    elif not intro_epigraph:
        intro_epigraph = stock_epigraph(topic)
        print(f"🖤 Opening card (stock line): {intro_epigraph['text'][:70]!r}")
    if len(chapters) >= 3 and not verdict_epigraph and not real_only:
        opening_text = " ".join(intro_epigraph.get("sentences")
                                or [intro_epigraph.get("text") or ""])
        verdict_epigraph = stock_epigraph(topic, verdict=True,
                                          exclude_texts={opening_text})
        print(f"🖤 Pre-verdict card (stock line): "
              f"{verdict_epigraph['text'][:70]!r}")

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
    # A trend video is a lesson from the past about something in the news
    # now; packaged as plain history ("France's May 1968 Student Uprising
    # Sparks Nationwide Strikes", 8 Oct, while France's students were out
    # again) nobody sees why to click it today. This week's headlines tie it.
    now = []
    if story and story.get("trend"):
        try:
            from trend_source import news_now
            now = news_now(story["trend"])
        except Exception as e:
            print(f"⚠️ News hook skipped ({type(e).__name__}: {e}).")
        print(f"📰 In the news now ({story['trend']}): "
              + (" | ".join(now) if now else "nothing this week — packaged as history."))
    meta = {}
    try:
        meta = _packaging_call(pool, topic, full_text, story=story, now=now)
        if not meta.get("title"):
            # an empty or placeholder reply: once more, on another model
            print("   ↩️  Packaging reply had no real title — asking another model.")
            meta = _packaging_call(pool, topic, full_text, story=story, now=now,
                                   skip={_last_model} if _last_model else ()) or meta
        # One correction pass, and only when the title is the problem. The
        # published long-form titles include "Soviet Invasion of Kuril Islands
        # 1945: The Hidden WW2 Battle" — a phrase that would fit any battle
        # ever fought, attached to a name almost nobody recognises. That
        # combination is the whole reason these videos are not clicked.
        if not validate_title((meta.get("title") or "").strip()):
            retry = _packaging_call(pool, topic, full_text, retitle=True, story=story,
                                    now=now)
            if retry.get("title") and validate_title(retry["title"].strip()):
                meta = retry
            else:
                print("   Keeping the first title — the retry was no better.")
    except QuotaExhausted:
        # Six chapters are already written. Losing the video over its title
        # would be absurd — fall back to topic-derived metadata.
        print("🛑 Quota gone before packaging; deriving metadata from the topic.")

    try:
        _chapter_stamps(pool, topic, chapters, era_year,
                        cast=(story or {}).get("plan", {}).get("cast") if story else None)
    except QuotaExhausted:
        print("🛑 Quota gone before the date stamps; publishing without them.")
    except Exception as e:
        print(f"⚠️ Date stamps skipped ({type(e).__name__}: {e}).")
    # Maps never depend on that request either: when it placed none (it came
    # back empty on the 2026-10-01 Rommel video, a story across North Africa,
    # Normandy and Germany), the outline's own places are mapped, each where
    # the narration first speaks it, at most four, one per chapter.
    if story and not any(s.get("map") for c in chapters for s in c["scenes"]):
        places = [p for p in (story.get("plan") or {}).get("places") or [] if p]
        if places:
            print(f"🗺️  No map from the maps request — mapping the story's places: "
                  f"{', '.join(places)}.")
        for place in places:
            if sum(1 for c in chapters for s in c["scenes"] if s.get("map")) >= 4:
                break
            try:
                _place_map({"target": place, "origin": "", "action": "mark", "light": []},
                           chapters, topic, first=2, anywhere=True)
            except Exception as e:
                print(f"   ↩️  Map of {place!r} skipped ({type(e).__name__}: {e}).")
    # Cast cards never depend on that request: without it (quota) they are
    # still placed, the decision maker read from the decision chapter.
    if story and not any(s.get("dossier") for c in chapters for s in c["scenes"]):
        try:
            _place_dossier("", chapters, cast=story["plan"].get("cast"))
        except Exception as e:
            print(f"   ↩️  Cast cards skipped ({type(e).__name__}: {e}).")

    words = sum(len(s["narration"].split()) for c in chapters for s in c["scenes"])
    # without a real title, the topic's own name ("Rommel's forced suicide
    # (1944)"), cut at a word, never a sentence chopped mid-word
    fallback = re.split(r"[,:;—]", topic)[0].strip()
    if len(fallback) > 70:
        fallback = fallback[:70].rsplit(" ", 1)[0]
    title = (meta.get("title") or fallback).strip()

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
        # story mode: the texts of the three thumbnails (thumb_variants.py),
        # every number checked against the fact sheet and the narration
        "thumbnail_variants": _checked_thumb_texts(
            meta.get("thumbnail_variants"), story, full_text + " " + " ".join(now)),
        # Posted as the first comment under the video. See
        # youtube_publisher.post_first_comment for why the channel needs one.
        "comment_question": str(meta.get("comment_question") or "").strip()[:280],
        # the story's places, main one first: the thumbnail map points there
        # (10 Oct, Gaza 1987: its one map scene was Tunis, so was the thumbnail)
        "places": [p for p in ((story or {}).get("plan") or {}).get("places") or [] if p],
        "chapters": chapters,
        "word_count": words,
        "era_year": era_year,
        "format": episode_format,
        "intro_epigraph": intro_epigraph,
        "verdict_epigraph": verdict_epigraph,
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


# A story chapter at least this long is written in two halves when every
# attempt at it whole has failed or came back under half its length (15 Oct:
# "The First Shot", 260 words, came back at 117 and the run stopped).
SPLIT_MIN_WORDS = 200


def _chapter_in_halves(pool, system_prompt, prompt_for, words, tail, covered,
                       enforce_history):
    """One chapter as two requests of half the length, joined, or None.
    prompt_for(words, tail, feedback, covered) builds the chapter prompt."""
    half = words // 2
    parts = []
    for n in (1, 2):
        if n == 1:
            note = (f"THIS CHAPTER IS WRITTEN IN TWO PARTS. Write ONLY PART 1: "
                    f"the first half of the beats above, in order, about {half} "
                    "words. Stop at the middle of the beats; the chapter does "
                    "not end here, so ignore the rule about its last sentence.")
            lines = list(covered)
        else:
            done = " ".join(x["narration"] for x in parts[0]["segments"])
            note = ("THIS CHAPTER IS WRITTEN IN TWO PARTS. PART 1 is written: "
                    f"\"{done[-1200:]}\" Write ONLY PART 2: the remaining beats, "
                    f"about {half} words, carrying straight on from part 1 "
                    "without repeating anything in it, and end the chapter as "
                    "the rules above say.")
            lines = list(covered) + re.split(r"(?<=[.!?])\s+", done)
            tail = parts[0]["segments"][-1]["narration"][-160:]
        try:
            got = _call(pool, system_prompt, prompt_for(half, tail, note, lines),
                        max_tokens=CHAPTER_MAX_TOKENS, expect=_chapter_reply_problem)
        except QuotaExhausted:
            raise
        except Exception as e:
            print(f"   ↩️  Part {n} failed ({type(e).__name__}: {e}).")
            return None
        if not _chapter_usable(got, enforce_history):
            print(f"   ↩️  Part {n} of the chapter came back unusable.")
            return None
        parts.append(got)
    joined = {**parts[1], "segments": parts[0]["segments"] + parts[1]["segments"]}
    got_words = _chapter_words(joined)
    if got_words < words * 0.5:
        print(f"   ↩️  The two halves came to {got_words} of ~{words} words.")
        return None
    print(f"   ✅ Written in two halves: {got_words} words.")
    return joined


def _real_meta(meta):
    """The packaging reply without placeholder echoes: the 2026-10-01 trend
    video went up titled "...", its description and spoken closing question
    "..." too, because one weak model copied the output template back. A
    text field needs real words; a list keeps only real items."""
    def real(v):
        if not isinstance(v, str):
            return False
        bare = v.replace("...", " ").replace("\u2026", " ")
        return len(re.findall(r"[A-Za-z]", bare)) >= 3 or len(re.findall(r"\d", bare)) >= 2
    out = {}
    for k, v in (meta or {}).items():
        if isinstance(v, str):
            if real(v):
                out[k] = v
        elif isinstance(v, list):
            out[k] = [x for x in v if not isinstance(x, str) or real(x)]
        else:
            out[k] = v
    return out


def _now_rules(now):
    """The packaging rules for a trend video while its subject is in the news."""
    heads = "\n".join(f"- {h}" for h in now)
    return (
        "THIS VIDEO IS THE HISTORY BEHIND A STORY IN THE NEWS THIS WEEK. "
        f"This week's headlines:\n{heads}\n"
        "People click because of what is happening NOW, so the packaging "
        "ties the two together. These rules override the title and thumbnail "
        "rules above where they differ:\n"
        "- title: what is happening now, in plain words from the headlines, "
        "then what happened last time. Under 75 characters. The shape, from a "
        "different story (never reuse its words): 'Ships Are Being Hit in the "
        "Red Sea Again. In 1987 It Started a War'.\n"
        "- thumbnail_text: today's hook, e.g. 'IT HAPPENED BEFORE'.\n"
        "- thumbnail_variants: person setup = what is happening now (2-4 "
        "words, e.g. 'THE SHIPS ARE BURNING AGAIN'), twist = what it led to "
        "last time; map question = today's hook as a question (e.g. 'IS IT "
        "1987 AGAIN?'), tag = what happened last time; faceoff as above.\n"
        "- description: the first sentence says what is happening now, as "
        "the headlines report it; the rest is the history.\n"
        "Say nothing about today beyond what these headlines state, never "
        "take a side or blame anyone in the present-day story, and every "
        "claim about the past must be in the narration.\n")


def _packaging_call(pool, topic, full_text, retitle=False, story=None, skip=(),
                    now=()):
    """Title, description, tags and thumbnail text, written against the finished
    script rather than guessed up front.

    retitle re-runs the call after the first title failed the shared rules,
    naming the failure so the second attempt is not the first one reworded.
    """
    correction = ""
    if retitle:
        if CLICKBAIT_MODE:
            correction = (
                "\nYOUR PREVIOUS TITLE WAS REJECTED — most likely it leaned "
                "entirely on a proper noun the audience does not recognise, or "
                "it repeated the grammatical shape of recent titles. Write a "
                "different title, not a rewording: state the specific thing "
                "that went wrong and what it cost, in words someone outside "
                "the subject understands, and go hard on the sell.\n")
        else:
            correction = (
                "\nYOUR PREVIOUS TITLE WAS REJECTED. It either used a phrase that "
                "promises significance without naming anything — 'the hidden', 'the "
                "untold story', 'what really happened', 'the forgotten', 'the "
                "secret', 'the truth about', 'shocking' — or it leaned entirely on "
                "a proper noun the audience does not recognise. Write a different "
                "title, not a rewording: state the specific thing that went wrong "
                "and what it cost, in words someone outside the subject understands."
                "\n")
    return _real_meta(_call(pool,
                 "You write YouTube metadata. Output only JSON, no fences.",
                 f"This is the narration of a documentary about:\n{topic}\n\n"
                 f"{full_text[:3000]}\n\n"
                 f"{correction}"
                 "Write metadata for a YouTube SEARCH audience. The title must name the "
                 "event with the words someone would actually type, and state the "
                 "consequence. Under 70 characters. No ALL CAPS.\n"
                 "THE TITLE MUST BE UNDERSTANDABLE TO SOMEONE WHO HAS NEVER HEARD OF "
                 "THIS EVENT. Include the searchable name, but never let the name be "
                 "the only content — the name is what gets the video found, the stake "
                 "is what gets it clicked, and it needs both. 'Dogger Bank Incident "
                 "1904' names something nobody outside the subject recognises; "
                 "'Russia's Fleet Opened Fire on British Fishing Boats' says what "
                 "happened.\n"
                 "IF THE STORY TURNS ON ONE PERSON, DO NOT NAME THEM UNLESS THE NAME "
                 "ITSELF IS FAMOUS. Test: would someone with zero history knowledge "
                 "recognise this name on sight? 'Hitler' or 'Einstein' pass; almost "
                 "every other historical figure fails, including names history buffs "
                 "would know — generals, ministers, scientists. When it fails, name "
                 "their plainest recognisable role instead ('A Nazi War Criminal', 'A "
                 "U.S. President's Doctor', 'A Soviet Engineer') and let the video "
                 "reveal who they actually were — that role swap IS the curiosity "
                 "gap, do not also spend it on a name nobody recognises. State the "
                 "consequence in plain outcome language too, never historical "
                 "shorthand that requires already knowing the story ('the Nuremberg "
                 "gallows', 'the Munich Agreement') — say literally what happened. "
                 "Bad: 'Hermann Göring Cheated the Nuremberg Gallows by Two Hours' — "
                 "recognised only by history buffs, and 'the Nuremberg gallows' "
                 "requires already knowing what Nuremberg was. Good: 'A Nazi War "
                 "Criminal Died Just 2 Hours Before His Execution'.\n"
                 + (STORY_TITLE_RULES if story else
                 "GO HARD ON THE SELL. The narration above opens with 'The Cost' and "
                    "'The Claim' — the second of those states the harshest reasonable "
                    "reading of this story as a live question. Build the title FROM "
                    "THAT CLAIM: turn it into the provocative question the whole video "
                    "answers. A question-led title may prime an assumption the video "
                    "complicates or answers 'no' to — that is exactly the shape to use, "
                    "not a shape to avoid. A superlative, a hard number, dramatic "
                    "phrasing are all wanted too.\n") +
                 f"{_RESTRAINED_TITLE_ADDENDUM}"
                 "Also write thumbnail_text: THREE OR FOUR WORDS ONLY, readable at "
                 "thumbnail size. Not a sentence, no punctuation. Extract the "
                 "strongest emotionally loaded interpretation the story supports, "
                 "in one of these shapes: [Actor] + [loaded noun] ('NASA'S FATAL "
                 "DECISION'), [Actor] + [dramatic verb] ('ENGINEERS IGNORED'), or "
                 "[dramatic event] + [consequence] ('SUNK IN 20 MINUTES'). Pick "
                 "whichever shape carries this story's actual point of blame or "
                 "consequence — do not force an actor in if the story has none. "
                 "[Actor] follows the same recognisability test as the title: an "
                 "unrecognised person's name never goes here — use their role "
                 "instead ('A NAZI WAR CRIMINAL', not 'GÖRING'S ESCAPE'). It "
                 "must not simply repeat the title; the two are read together, so "
                 "they should say different things. Bad: 'The story of how a ship "
                 "sank', 'AMAZING HISTORY FACT' — too flat, or too generic to be "
                 "about this story.\n"
                 "And thumbnail_badge: a year or a single number, e.g. '1628'.\n"
                 + (STORY_THUMB_RULES if story else "") +
                 (_now_rules(now) if now else "") +
                 "And comment_question: ONE question to post as the first comment, "
                 "answerable by someone who just watched this and has an opinion — "
                 "who was at fault, which moment was the point of no return, what "
                 "they would have done. Name the people or the choice. Never "
                 "'what do you think?' on its own.\n"
                 'Output: {"title": "...", "description": "3 sentences, no hashtags", '
                 '"tags": ["10-14 search tags including proper nouns"], '
                 '"thumbnail_text": "...", "thumbnail_badge": "...", '
                 + ('"thumbnail_variants": {"faceoff": {"big": "...", "box": "..."}, '
                    '"person": {"setup": "...", "twist": "..."}, '
                    '"map": {"question": "...", "tag": "..."}}, '
                    if story else "") +
                 '"comment_question": "..."}',
                 # Same headroom logic as the anchor call: reasoning models
                 # think inside max_tokens, and a truncated reply costs a
                 # request from the daily budget and returns nothing.
                 max_tokens=2500, skip=skip,
                 expect={"title": str, "description": str, "tags": list}) or {})


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


# Date-and-place stamps: only on the chapters where the story is somewhere
# (the plan, the decision, the chain). The cold open and the verdict are the
# narrator speaking now, so a stamp there would orient no one.
STAMP_ROLES = ("build", "pivot", "drive")
STAMP_RELATIVE = ("THE SAME NIGHT", "THE SAME DAY", "THE NEXT MORNING",
                  "THAT NIGHT", "THAT MORNING", "HOURS LATER", "DAYS LATER",
                  "WEEKS LATER", "MONTHS LATER", "YEARS LATER")
_MONTHS = ("january", "february", "march", "april", "may", "june", "july",
           "august", "september", "october", "november", "december")


_ONES = ("", "one", "two", "three", "four", "five", "six", "seven", "eight",
         "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
         "sixteen", "seventeen", "eighteen", "nineteen")
_ORD = {"one": "first", "two": "second", "three": "third", "five": "fifth",
        "eight": "eighth", "nine": "ninth", "twelve": "twelfth",
        "twenty": "twentieth", "thirty": "thirtieth"}


def _day_forms(day: int):
    """Every way a narration might say a day of the month: "23", "23rd",
    "twenty-three", "twenty-third", "twenty third"."""
    suffix = ("th" if 10 <= day % 100 <= 20
              else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th"))
    tens, ones = divmod(day, 10)
    if day < 20:
        card = _ONES[day]
        ords = [_ORD.get(card, card + "th")]
    else:
        ten = {2: "twenty", 3: "thirty"}[tens]
        if ones:
            card = f"{ten}-{_ONES[ones]}"
            ords = [f"{ten}-{_ORD.get(_ONES[ones], _ONES[ones] + 'th')}"]
        else:
            card, ords = ten, [_ORD[ten]]
    forms = {f" {day} ", f" {day}{suffix}", card, *ords}
    forms |= {f.replace("-", " ") for f in list(forms)}
    return forms


def _says_day(text: str, day: int) -> bool:
    """Whether the text states this day, as a whole word — "third" inside
    "twenty-third" is the 23rd, not the 3rd."""
    for form in _day_forms(day):
        form = form.strip()
        for m in re.finditer(rf"(?<![a-z0-9]){re.escape(form)}(?![a-z0-9])",
                             text):
            before = text[max(0, m.start() - 7):m.start()]
            if day < 20 and re.search(r"(twenty|thirty)[- ]$", before):
                continue
            return True
    return False


def _stamp_grounded(place, date, narration, topic, era_year):
    """A stamp is published only when every place word and every date word
    is in the chapter's own narration (or the topic). A stamp is on screen
    as fact, and this pipeline publishes without anyone reading it, so a
    model's plausible-looking date is dropped, not trusted."""
    text = f" {narration} {topic} ".lower()
    words = set(re.findall(r"[a-z0-9]+", text))
    place_words = re.findall(r"[a-z0-9]+", (place or "").lower())
    if not place_words or len(place_words) > 4 or len(place) > 28:
        return False
    if any(w not in words for w in place_words if len(w) > 2):
        return False
    if not date or date.upper() in STAMP_RELATIVE:
        return True
    tokens = re.findall(r"[a-z0-9]+", date.lower())
    month_ok = any(m in words for m in _MONTHS if m in tokens)
    for t in tokens:
        if t.isdigit() and len(t) == 4:
            if t not in words and str(era_year or "") != t:
                return False
        elif t.isdigit():
            # A day of the month is usually spoken as words ("the
            # twenty-third of August"), so it must appear in any of its
            # spoken or written forms, and its month must be stated too.
            if not month_ok or not 1 <= int(t) <= 31:
                return False
            if not _says_day(text, int(t)):
                return False
        elif t not in words:
            return False
    return True


def _named_in(name, text):
    """Whether every word of a place name appears in the text, whole."""
    words = re.findall(r"[a-z0-9]+", (name or "").lower())
    return bool(words) and all(
        re.search(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])", text)
        for w in words)


def _place_map(plan, chapters, topic, after_decision=False, first=1,
              anywhere=False):
    """Attaches scene["map"] to the first scene that speaks the map's
    target: before the decision for the first map, after it (in the chain,
    not the verdict) for the second. Every name must be in that part's
    narration (or the topic); an origin or region that is not is dropped,
    and a target that is not means no map at all. A scene already holding
    a map or the chain is never reused."""
    if not isinstance(plan, dict):
        return
    pivot = next((i for i, c in enumerate(chapters)
                  if (c.get("pacing") or {}).get("role") == "pivot"),
                 len(chapters))
    # Chapter 0 is the cold open: the hook stays photos. The verdict is the
    # narrator speaking now: no map there either.
    span = chapters[pivot + 1:-1] if after_decision else chapters[first:pivot]
    if anywhere:
        # Story mode: a map for each front or turning point, wherever the
        # story reaches it (after the teaser and the cast, before the
        # verdict), at most one per chapter and never the same place twice.
        span = [c for c in chapters[first:-1]
                if not any(s.get("map") for s in c["scenes"])]
    candidates = [(c, s) for c in span for s in c["scenes"]
                  if not s.get("map") and not s.get("chain")]
    if (after_decision or anywhere) and any(s.get("map", {}).get("target", "").lower()
                              == str(plan.get("target") or "").lower()
                              for c in chapters for s in c["scenes"]):
        return
    text = " ".join(s["narration"] for _, s in candidates).lower() \
        + " " + (topic or "").lower()
    target = " ".join(str(plan.get("target") or "").split())
    if not target or len(target) > 40 or not _named_in(target, text):
        if target:
            print(f"   ↩️  Map target {target!r} is not in the narration — no map.")
        return
    origin = " ".join(str(plan.get("origin") or "").split())
    if origin and (len(origin) > 40 or not _named_in(origin, text)
                   or origin.lower() == target.lower()):
        origin = ""
    action = str(plan.get("action") or "mark").lower()
    if action not in ("strike", "move", "mark"):
        action = "mark"
    light = [" ".join(str(x).split()) for x in (plan.get("light") or [])
             if isinstance(x, str)]
    light = [x for x in light if 0 < len(x) <= 40 and _named_in(x, text)][:2]
    # A chapter's first scene carries its on-screen title (middle-left, ~4s),
    # which would sit over the map's action — so a later scene that speaks
    # the target is preferred, and a first scene is the fallback.
    speaking = [(c, s) for c, s in candidates
                if _named_in(target, s["narration"].lower())]
    speaking.sort(key=lambda cs: cs[0]["scenes"][0] is cs[1])
    if not speaking:
        where = "after" if after_decision else "before"
        print(f"   ↩️  No scene {where} the decision speaks {target!r} — no map.")
        return
    # Placed now, not at render time: a name no source can locate is
    # dropped here, so the caller can map the story's next place instead
    # (the 2026-10-01 Rommel video's "North Africa" had no coordinates).
    try:
        from map_engine import geocode
        ll = geocode(target)
    except Exception as e:
        print(f"   ↩️  Could not place {target!r} ({type(e).__name__}: {e}).")
        ll = None
    if not ll:
        print(f"   ↩️  No coordinates for {target!r} — no map there.")
        return
    speaking[0][1]["map"] = {"target": target, "origin": origin,
                             "action": action, "light": light, "ll": list(ll)}
    print(f"🗺️  Map planned: {origin or '—'} → {target} ({action}; "
          f"lit: {', '.join(light) or 'none'}).")


_CHAIN_STOP = {"the", "and", "into", "from", "with", "that", "this",
               "their", "they", "them", "then", "were", "was", "had", "has",
               "have", "for", "its", "his", "her"}


def _chain_words(text):
    return [w for w in re.findall(r"[a-z0-9]+", (text or "").lower())
            if len(w) >= 4 and w not in _CHAIN_STOP]


_MONTHS = ("january", "february", "march", "april", "may", "june", "july",
           "august", "september", "october", "november", "december")
_UNITS = ("", "first", "second", "third", "fourth", "fifth", "sixth", "seventh",
          "eighth", "ninth", "tenth", "eleventh", "twelfth", "thirteenth",
          "fourteenth", "fifteenth", "sixteenth", "seventeenth", "eighteenth",
          "nineteenth")


def _chain_day_forms(n):
    """How a narrator may say day n: "22", "22nd", "twenty-second"."""
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    words = (_UNITS[n] if n < 20 else
             {20: "twentieth", 30: "thirtieth"}.get(n) or
             f"{'twenty' if n < 30 else 'thirty'}-{_UNITS[n % 10]}")
    return {str(n), f"{n}{suffix}", words, words.replace("-", " ")}


def _chain_date(raw, narration, era_year):
    """(label "22 MAY", ordinal day, year) for a date the narration really
    says, or None. The month and the day must both be spoken, within a few
    words of each other; a date the model supplied from memory, or moved to
    another step, never reaches the screen."""
    text = (raw or "").lower()
    month = next((i for i, m in enumerate(_MONTHS) if m in text), None)
    if month is None:
        return None
    day = None
    m = re.search(r"\b(\d{1,2})(st|nd|rd|th)?\b", text)
    if m:
        day = int(m.group(1))
    else:
        for n in range(31, 0, -1):
            if any(f in text for f in _chain_day_forms(n) - {str(n)} if not f.isdigit()):
                day = n
                break
    if not day or day > 31:
        return None
    ym = re.search(r"\b(1\d{3}|20\d{2})\b", text)
    year = int(ym.group(1)) if ym else era_year
    forms = _chain_day_forms(day)
    for mm in re.finditer(_MONTHS[month], narration):
        window = narration[max(0, mm.start() - 22):mm.end() + 8]
        if any(re.search(rf"(?<![a-z0-9-]){re.escape(f)}(?![a-z0-9-])", window) for f in forms):
            import datetime
            try:
                ordinal = datetime.date(year or 2001, month + 1, day).toordinal()
            except ValueError:
                return None
            return f"{day} {_MONTHS[month].upper()}", ordinal, year
    return None


def _place_chain(steps, chapters, topic, dates=None, era_year=None):
    """Attaches scene["chain"] to the scene in "The Chain" whose narration
    matches the steps best (not the chapter's first scene, which carries
    its title; not a map scene). Every meaningful word of every step must
    appear somewhere in the narration or the topic — matched on 5-letter
    prefixes, so "escorts break" meets "escorts broke" but an invented
    event does not — or the chain is dropped whole."""
    if not isinstance(steps, list):
        return
    steps = [" ".join(str(x).split()) for x in steps if isinstance(x, str)]
    steps = [x for x in steps if 1 <= len(x.split()) <= 7][:4]
    if len(steps) < 3:
        return
    known = {w[:5] for c in chapters for s in c["scenes"]
             for w in _chain_words(s["narration"])}
    known |= {w[:5] for w in _chain_words(topic)}
    for step in steps:
        words = _chain_words(step)
        missing = [w for w in words if w[:5] not in known]
        # One unmatched word is tolerated when the rest is grounded — a verb
        # in another tense ("break" for the narration's "broke") — but two,
        # or a step with fewer than two grounded words, is an invention.
        if len(missing) > 1 or len(words) - len(missing) < min(2, len(words)):
            print(f"   ↩️  Chain dropped: {step!r} says {missing} — not in the "
                  f"narration.")
            return
    drive = [c for c in chapters if (c.get("pacing") or {}).get("role") == "drive"]
    if not drive:
        return
    step_words = {w[:5] for st in steps for w in _chain_words(st)}
    options = [s for s in drive[0]["scenes"][1:]
               if not s.get("map") and not s.get("chain")]
    if not options:
        print("   ↩️  Chain skipped: no free scene in the chain chapter.")
        return
    best = max(options, key=lambda s: len(step_words & {
        w[:5] for w in _chain_words(s["narration"])}))
    best["chain"] = steps
    # Dates for the timeline: only those the narration itself says, and
    # only when at least two survive in order; otherwise the steps are
    # numbered without dates.
    narration = " ".join(s["narration"] for c in chapters for s in c["scenes"]).lower()
    got = [(_chain_date(d, narration, era_year) if isinstance(d, str) else None)
           for d in (list(dates or []) + [None] * len(steps))[:len(steps)]]
    known = [g for g in got if g]
    if len(known) >= 2 and all(a[1] <= b[1] for a, b in zip(known, known[1:])):
        best["chain_dates"] = [{"label": g[0], "day": g[1]} if g else None for g in got]
        years = sorted({g[2] for g in known if g[2]})
        best["chain_year"] = (str(years[0]) if len(years) == 1 else
                              f"{years[0]}–{years[-1]}" if years else "")
    else:
        dropped = [d for d, g in zip(dates or [], got) if d and not g]
        if dropped:
            print(f"   ↩️  Timeline dates left out (not spoken as given): {dropped}.")
    print(f"⛓️  Chain planned: {' → '.join(steps)}"
          + (f" ({', '.join(d['label'] if d else '—' for d in best['chain_dates'])})"
             if best.get("chain_dates") else "") + ".")


def _place_dossier(name, chapters, cast=None):
    """Attaches scene["dossier"] to the first scene, after the cold open and
    up to the decision, that speaks the decision-maker's name — a later
    scene of a chapter preferred over its first, whose title card sits
    middle-left where the photo print lands. Photo and description are
    fetched later from Wikipedia (dossier_engine), so the card carries the
    full name.

    In story mode the cast settles the name: a bare surname ("Dayan") is
    resolved to the cast's full name, and when the model names no single
    person (a cabinet vote), the cast member spoken most up to the decision
    gets the card. The first story-mode run lost its card to exactly that."""
    name = " ".join(str(name or "").split())
    pivot = next((i for i, c in enumerate(chapters)
                  if (c.get("pacing") or {}).get("role") == "pivot"),
                 len(chapters) - 1)
    window = [s for c in chapters[1:pivot + 1] for s in c["scenes"]]

    def surname(n):
        parts = re.findall(r"[a-z]+", (n or "").lower())
        return parts[-1] if parts else ""

    if cast:
        full = next((c["name"] for c in cast
                     if surname(c.get("name")) == surname(name)), None)
        if not full:
            # Only the decision chapter itself counts, and a tie names
            # nobody: labelling the wrong leader DECISION MAKER would be a
            # factual error on screen.
            decision_scenes = chapters[pivot]["scenes"] if 0 < pivot < len(chapters) else []
            spoken = sorted(
                ((sum(len(re.findall(rf"\b{re.escape(surname(c['name']))}\b",
                                     s["narration"].lower())) for s in decision_scenes),
                  c["name"]) for c in cast if surname(c.get("name"))),
                reverse=True)
            full = (spoken[0][1] if spoken and spoken[0][0]
                    and (len(spoken) < 2 or spoken[0][0] > spoken[1][0]) else None)
            if full:
                print(f"   🗂️  Decision-maker from the cast: {full} "
                      f"(model named {name or 'nobody'!r}).")
        name = full or name
        _place_cast(cast, full, chapters)
        return
    if not name or len(name) > 40 or len(name.split()) < 2:
        print(f"   ↩️  No decision-maker card: no full name ({name!r}).")
        return
    key = surname(name)
    speaking = [(c, s) for c in chapters[1:pivot + 1] for s in c["scenes"]
                if not s.get("map") and not s.get("chain")
                and (_named_in(name, s["narration"].lower())
                     or (cast and re.search(rf"\b{re.escape(key)}\b",
                                            s["narration"].lower())))]
    if not speaking:
        print(f"   ↩️  No decision-maker card: {name!r} is never spoken "
              f"before the decision.")
        return
    first_idx = chapters.index(speaking[0][0])
    same = [cs for cs in speaking if chapters.index(cs[0]) == first_idx]
    same.sort(key=lambda cs: cs[0]["scenes"][0] is cs[1])
    same[0][1]["dossier"] = {"name": name, "decision": True}
    print(f"🗂️  Decision-maker card planned: {name}.")


CHAIN_ENABLED = False


def _place_front_stamps(chapters, places):
    """Story mode: a place stamp ("JERUSALEM") on the scene where the story
    moves to another of its named places mid-chapter, so a viewer following
    several fronts always knows where they are (the owner was lost in the
    fourth and fifth chapters of the third cut). Only when the place opens
    the scene's narration ("In Jerusalem, ...", "On the Golan Heights, ..."),
    at most one per chapter, never on the chapter's first scene (it has the
    chapter stamp), a map or a card."""
    keys = []
    for p in places or []:
        words = [w for w in re.findall(r"[a-z]+", p.lower()) if len(w) >= 5]
        if words:
            keys.append((p, words))
    placed = []
    for ch in chapters[1:]:
        current = (ch.get("stamp") or "").split("·")[0].strip().lower()
        for i, sc in enumerate(ch["scenes"]):
            if i == 0 or sc.get("map") or sc.get("dossier") or sc.get("chain"):
                continue
            opening = " ".join(re.findall(r"[a-z]+", sc["narration"].lower())[:9])
            hit = next((p for p, ws in keys if any(re.search(rf"\b{w}", opening) for w in ws)), None)
            if hit and hit.lower() not in current and not any(w in current for w in hit.lower().split()):
                sc["front_stamp"] = hit.upper()
                placed.append(f"{ch['heading']}: {hit}")
                break
    if placed:
        print(f"📍 Front stamps: {'; '.join(placed)}.")


MAX_CAST_CARDS = 5


def _place_cast(cast, decision_name, chapters):
    """Story mode: every cast member's card sits in the cast chapter (the
    video's second chapter, docs/VIDEO_SPEC.md 2), on the segment that
    introduces them — one card per segment, never the chapter's first
    segment (it carries the chapter title), nowhere else in the video. Only
    the decision-maker's card reads DECISION MAKER; the others carry their
    role. The photo swaps sides and the file number counts up."""
    def surname(n):
        parts = re.findall(r"[a-z]+", (n or "").lower())
        return parts[-1] if parts else ""

    people = [c for c in cast if surname(c.get("name"))][:MAX_CAST_CARDS]
    decider = surname(decision_name)
    placed = []
    if len(chapters) > 1:
        for i, sc in enumerate(chapters[1]["scenes"]):
            if i == 0 or sc.get("map") or len(placed) >= len(people):
                continue
            text = sc["narration"].lower()
            hits = []
            for c in people:
                if c["name"] in placed:
                    continue
                m = re.search(rf"\b{re.escape(surname(c['name']))}\b", text)
                if m:
                    hits.append((m.start(), c))
            if not hits:
                continue
            _, c = min(hits, key=lambda h: h[0])
            n = len(placed) + 1
            sc["dossier"] = {"name": c["name"], "shown": c["name"],
                             "role": (c.get("role") or "")[:30],
                             "decision": _is_decider(c["name"], decision_name, people),
                             "file_no": n, "side": "left" if n % 2 else "right"}
            placed.append(c["name"])
    missing = [c["name"] for c in people if c["name"] not in placed]
    print(f"🎭 Cast cards planned in the cast chapter: {', '.join(placed) or 'none'}"
          + (f" (decision maker: {decision_name})" if decision_name else "")
          + (f"; not introduced there: {', '.join(missing)}" if missing else "") + ".")


_RANKS = re.compile(r"^(captain|capt\.?|general|gen\.?|colonel|col\.?|major|lieutenant|lt\.?|"
                    r"admiral|sergeant|sgt\.?|private|governor|president|king|queen|"
                    r"prince|princess|sir|lord|lady|dr\.?|field marshal|marshal|"
                    r"emperor|empress|chief|judge|justice)\s+", re.I)


def _plain_name(n):
    """'Captain Thomas Preston' -> 'thomas preston'."""
    n = (n or "").strip()
    while _RANKS.match(n):
        n = _RANKS.sub("", n, count=1)
    return " ".join(re.findall(r"[a-z]+", n.lower()))


_ACTED_ON = re.compile(r"\b(accused|convicted|condemned|prisoner|imprisoned|victim|"
                       r"defendant|framed|exiled|executed|wrongly|scapegoat|"
                       r"hostage|deported)\b", re.I)


def _is_decider(name, decision_name, cast):
    """Whether this cast member is the decision maker: the full name when
    two of the cast share a surname (John and Samuel Adams were both
    stamped DECISION MAKER on the 2026-10-02 video), the surname otherwise."""
    a, b = _plain_name(name), _plain_name(decision_name)
    if not a or not b:
        return False
    # the person a decision was made against is never stamped DECISION
    # MAKER (Dreyfus was, on the 2026-10-07 video)
    role = next((c.get("role") or "" for c in cast
                 if _plain_name(c.get("name")) == a), "")
    if _ACTED_ON.search(role):
        return False
    if a == b or a.endswith(" " + b) or b.endswith(" " + a):
        return True
    sur = lambda n: n.split()[-1]
    shared = sum(1 for c in cast if _plain_name(c.get("name")).split()[-1:] == [sur(a)])
    return shared == 1 and sur(a) == sur(b)


def _chapter_stamps(pool, topic, chapters, era_year, cast=None):
    """Writes chapter["stamp"] ("KINMEN · 23 AUGUST 1958") on the chapters in
    STAMP_ROLES whose narration states where and when they happen. One
    request for the whole video; anything the grounding check rejects is
    simply left without a stamp."""
    wanted = [(i, c) for i, c in enumerate(chapters)
              if (c.get("pacing") or {}).get("role") in STAMP_ROLES]
    if not wanted:
        return
    listing = "\n\n".join(
        f"[{i}] {c['heading']}\n"
        + " ".join(s["narration"] for s in c["scenes"])[:900]
        for i, c in wanted)
    prompt = (
        f"Story: {topic}\n\nChapters of a documentary:\n\n{listing}\n\n"
        "For each chapter, give an on-screen stamp of WHERE and WHEN its "
        "events happen, the way a documentary types it at the corner of "
        "the screen.\n"
        "HARD RULES:\n"
        "- Use ONLY a place and a date that the chapter's own text states. "
        "Never add a day, month, year or place it does not say.\n"
        "- place: 1-3 words, the most specific place named (a city, island, "
        "coast, ship or building).\n"
        "- date: a date written like '23 AUGUST 1958' or 'SUMMER 1958' or "
        "'1958', or one of: " + ", ".join(STAMP_RELATIVE) + ". Empty if the "
        "chapter states no time.\n"
        "- Skip a chapter that names no place.\n\n"
        + (("ALSO describe up to FOUR maps, as NAMES only (never "
            "coordinates), all taken from the text above: one for each front, "
            "attack or turning point the story moves to, in story order, each "
            "a clearly different place, each in a different chapter. Each map "
            "has:\n") if cast else
           ("ALSO describe up to TWO maps, as NAMES only (never coordinates), "
            "all taken from the text above: the first for where the story "
            "begins (before the decision), the second — only if the text names "
            "a clearly different place — for where the consequence lands "
            "(after the decision). Each map has:\n")) +
        "- target: the place where the decisive event happened (a city, "
        "island, strait, base or battlefield);\n"
        "- origin: where the force came from, if the text names it — a "
        "city or base, or the province or country if that is all it names; "
        "else empty;\n"
        "- action: \"strike\" (shelling, bombing, attack), \"move\" (an "
        "army, fleet or convoy travelling) or \"mark\" (neither);\n"
        "- light: up to 2 countries or provinces the text names that the "
        "viewer should see (e.g. the target's country across a border).\n\n"
        "ALSO write the chain of consequences: 3 or 4 steps, 2-6 words each, "
        "from the decision to what it finally cost, each one a thing the "
        "text above actually says happened, in its own words and tense "
        "(e.g. \"The shelling of Kinmen\", \"The Seventh Fleet in the "
        "strait\", \"Escorts broke the blockade\", \"Beijing called a "
        "ceasefire\"). "
        "No numbers, names or events the text does not state.\n"
        "chain_dates: one entry per chain step, the date the text gives for "
        "that step, copied as the text says it (e.g. \"22 May 1967\", "
        "\"the fifth of June\"), or \"\" when the text gives no day for it.\n\n"
        "ALSO name the decision_maker: the one person who MADE the story's "
        "central decision (ordered, chose, launched it), full name exactly "
        "as the text says it (e.g. \"Mao Zedong\"). Never the person the "
        "decision was made against: not the accused, the prisoner or the "
        "victim (in the Dreyfus affair, the War Minister who ordered the "
        "prosecution, not Dreyfus). Empty if the text names no single "
        "person.\n\n"
        'Output ONLY JSON: {"stamps": [{"chapter": 0, "place": "...", '
        '"date": "..."}], "maps": [{"target": "...", "origin": "...", '
        '"action": "strike", "light": ["..."]}], "chain": ["...", "..."], "chain_dates": ["...", "..."], '
        '"decision_maker": "..."}'
    )
    data = _call(pool, "You write documentary location stamps. Output only "
                 "JSON.", prompt, max_tokens=1500, expect={"maps": list} if cast else None)
    if not data and _last_model:
        # One more try on another model: the 2026-09-29 trend run lost every
        # map to a single busy (503) model at this call.
        print("   ↩️  Maps request came back empty — one more try on another model.")
        data = _call(pool, "You write documentary location stamps. Output only "
                     "JSON.", prompt, max_tokens=1500, skip={_last_model},
                     expect={"maps": list} if cast else None)
    maps = (data or {}).get("maps") or [(data or {}).get("map")]
    # story mode: no map in the teaser or over a cast introduction
    # (docs/VIDEO_SPEC.md 1-2)
    for i, plan in enumerate(maps[:4] if cast else maps[:2]):
        try:
            if cast:
                _place_map(plan, chapters, topic, first=2, anywhere=True)
            else:
                _place_map(plan, chapters, topic, after_decision=(i == 1))
        except Exception as e:
            print(f"   ↩️  Map skipped ({type(e).__name__}: {e}).")
    # The chain/timeline cards were dropped after the third Six-Day War cut
    # (too quick to read, and they did not track the video's own time);
    # the date ticker (date_ticker.py) does that job now.
    if CHAIN_ENABLED:
        try:
            _place_chain((data or {}).get("chain"), chapters, topic,
                         dates=(data or {}).get("chain_dates"), era_year=era_year)
        except Exception as e:
            print(f"   ↩️  Chain skipped ({type(e).__name__}: {e}).")
    try:
        _place_dossier((data or {}).get("decision_maker"), chapters, cast=cast)
    except Exception as e:
        print(f"   ↩️  Decision-maker card skipped ({type(e).__name__}: {e}).")
    by_index = dict(wanted)
    placed = 0
    for st in (data or {}).get("stamps") or []:
        try:
            i = int(st.get("chapter"))
        except (TypeError, ValueError):
            continue
        chapter = by_index.get(i)
        place = " ".join(str(st.get("place") or "").split()).upper()
        date = " ".join(str(st.get("date") or "").split()).upper()
        if not chapter or chapter.get("stamp"):
            continue
        narration = " ".join(s["narration"] for s in chapter["scenes"])
        if not _stamp_grounded(place, date, narration, topic, era_year):
            print(f"   ↩️  Stamp dropped, not in the narration: "
                  f"{place} · {date}")
            continue
        chapter["stamp"] = f"{place} · {date}" if date else place
        placed += 1
        print(f"🗓️  Stamp for {chapter['heading']!r}: {chapter['stamp']}")
    if not placed:
        print("   No chapter got a stamp this run.")


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
            if s_idx == 0 and chapter.get("stamp"):
                s["stamp"] = chapter["stamp"]
            # The visual engine gates archival search and b-roll on the era.
            s["era_year"] = script.get("era_year")
            if chapter.get("punch_phrase") and s_idx == chapter.get("punch_idx"):
                # Attached to the scene that actually contains the phrase —
                # not always scene 0 — since longform_video only needs to
                # search this one scene's word timings for the exact words,
                # not the whole chapter, to sync the callout.
                s["punch_phrase"] = chapter["punch_phrase"]
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
