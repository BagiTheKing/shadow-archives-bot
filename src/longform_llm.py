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
from llm_engine import get_free_models, normalize_thumbnail_fields, _extract_json
from llm_provider import get_pool, describe_provider, gemini_available
from visual_engine import (CANONICAL_STYLE, build_generation_prompt,
                           era_from_text)
from topics import pick_topic

# Words per chapter. edge-tts at +8% rate lands around 165 wpm, so ~1,200 words
# is roughly 7.5 minutes — inside the 6-9 minute target.
ARC = [
    # The brief used to say "state the anomaly in two sentences" while asking for
    # 110 words. The model obeyed the brief, produced ~54 words, and failed the
    # length check twice — a self-inflicted deadlock. The brief now asks for the
    # material that actually fills the chapter.
    ("Cold Open",
     "Open with the anomaly in one or two sharp sentences, the way a Short would. "
     "Then widen: sketch the scale of what was lost, name the people or place at "
     "the centre of it, and pose the question this video answers — not what "
     "happened, but how the decision behind it looked reasonable at the time. "
     "Do not summarise the ending.",
     90),
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
    """Gemini gets real headroom; OpenRouter's free models keep the tight
    ceiling they need to stay inside a 50-request day."""
    if provider == "gemini":
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
                raise QuotaExhausted(
                    f"OpenRouter free-model daily limit reached after "
                    f"{_request_count} request(s) this run."
                    f"{_quota_reset(err)}") from None
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


def _chapter_prompt(topic, heading, brief, words, previous_tail, feedback="",
                    era_year=None, covered=None):
    n_images = max(2, round(words / WORDS_PER_IMAGE))

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

    chapters, tail, short_chapters, dropped = [], "", 0, []
    # Everything already narrated, as normalised sentences and as plain lines.
    # The first feeds the repetition gate, the second is handed to the model so
    # it knows what not to say again.
    said_sentences, said_lines = [], []
    for heading, brief, words in ARC:
        print(f"✍️  Chapter: {heading} (~{words} words)...")
        data, best, feedback = None, None, ""

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
                                                  covered=said_lines),
                                  max_tokens=CHAPTER_MAX_TOKENS)
            except QuotaExhausted as e:
                print(f"🛑 {e}")
                # Nothing further can succeed today, so stop rather than burning
                # the remaining chapters against a wall.
                raise

            if _validate_chapter(candidate, words,
                                 enforce_history=enforce_history,
                                 previous_sentences=said_sentences):
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
            # A short chapter costs pacing. Abandoning the run costs the video.
            data = best
            short_chapters += 1
            print(f"   ⚠️ Accepting a short {heading!r} ({_chapter_words(best)} "
                  f"words) rather than losing the run.")

        if not data:
            print(f"❌ Chapter {heading!r} could not be generated.")
            if heading == ARC[0][0]:
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

        chapters.append({"heading": heading, "scenes": segs, "key_stat": stat})
        tail = segs[-1]["narration"][-160:]
        total = sum(len(x["narration"].split()) for x in segs)
        print(f"   ✅ {len(segs)} segments, {total} words")

    if len(chapters) < MIN_PUBLISHABLE_CHAPTERS:
        print(f"❌ Only {len(chapters)} of {len(ARC)} chapters survived — below "
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
    try:
        meta = _packaging_call(pool, topic, full_text)
    except QuotaExhausted:
        # Six chapters are already written. Losing the video over its title
        # would be absurd — fall back to topic-derived metadata.
        print("🛑 Quota gone before packaging; deriving metadata from the topic.")
        meta = {}

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


def _packaging_call(pool, topic, full_text):
    """Title, description, tags and thumbnail text, written against the finished
    script rather than guessed up front."""
    return _call(pool,
                 "You write YouTube metadata. Output only JSON, no fences.",
                 f"This is the narration of a documentary about:\n{topic}\n\n"
                 f"{full_text[:3000]}\n\n"
                 "Write metadata for a YouTube SEARCH audience. The title must name the "
                 "event with the words someone would actually type, and state the "
                 "consequence. Under 70 characters. No clickbait the video does not "
                 "deliver, no ALL CAPS.\n"
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


def flatten_scenes(script: dict) -> list:
    """Flattens chapters into the scene list the audio/visual engines expect.
    Chapter titles are typeset over the footage by the video engine (keyed off
    chapter_index changes), so every scene gets real imagery — the old
    full-screen chapter cards are gone."""
    scenes = []
    for c_idx, chapter in enumerate(script["chapters"]):
        for s_idx, scene in enumerate(chapter["scenes"]):
            s = dict(scene)
            s["chapter_index"] = c_idx
            s["chapter_heading"] = chapter["heading"]
            # The visual engine gates archival search and b-roll on the era.
            s["era_year"] = script.get("era_year")
            if s_idx == 0:
                # longform_video reads key_stat off the chapter's first scene.
                s["key_stat"] = chapter.get("key_stat") or {}
            scenes.append(s)
    return scenes


if __name__ == "__main__":
    s = generate_longform_script()
    if s:
        print(json.dumps(s, indent=2)[:2000])
