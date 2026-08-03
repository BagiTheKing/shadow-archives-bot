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

from llm_engine import get_free_models, _extract_json
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

STYLE_SUFFIX = ("dramatic chiaroscuro lighting, dark moody cinematic oil painting, "
                "rich texture, muted color palette with one accent color, highly "
                "detailed, no text, no watermark")

# One image per ~45 spoken words, so each frame holds ~16 seconds. Long-form
# cannot cut every 8 seconds for 8 minutes without becoming exhausting, and every
# extra image is another Pollinations request that can rate-limit the run.
WORDS_PER_IMAGE = 45

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
- State the shot type first, and vary it: extreme close-up, wide establishing shot, \
low-angle, overhead, silhouette, over-the-shoulder.
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

FAILURE_LOG_DIR = os.path.join("build", "llm_failures")

# A 280-word chapter with six image prompts is around 1,200 tokens of JSON, and
# 2,000 left almost no headroom: reasoning models spend hundreds of tokens
# thinking before they emit anything, run into the ceiling mid-string, and the
# reply arrives truncated. Free models cost nothing per token, so the ceiling
# should sit well clear of the longest chapter rather than just above it.
CHAPTER_MAX_TOKENS = 4000

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
            if _parse_failures.get(m, 0) < MAX_PARSE_FAILURES]
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


def _call(client, models, system, user, max_tokens):
    """Runs a prompt down the free-model chain, one attempt per model.

    Raises QuotaExhausted the moment the daily allowance is gone, instead of
    walking the remaining models to collect identical 429s.
    """
    global _preferred_model, _request_count

    for model in _order_models(models):
        try:
            _request_count += 1
            r = client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": user}],
                temperature=0.7,
                max_tokens=max_tokens,
            )
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
            if "429" in err:
                time.sleep(10)          # per-minute throttle, worth one pause
    return None


def _chapter_prompt(topic, heading, brief, words, previous_tail, feedback=""):
    n_images = max(2, round(words / WORDS_PER_IMAGE))
    continuity = (f"\nThe previous chapter ended with: \"{previous_tail}\"\n"
                  "Continue from there without repeating it.\n" if previous_tail else "")
    # Retrying with a byte-identical prompt just reproduces the same rejected
    # output, which is exactly what happened on the first live trending run.
    correction = f"\n{feedback}\n" if feedback else ""
    return (
        f"Story: {topic}\n"
        f"{continuity}{correction}\n"
        f"Write the chapter titled \"{heading}\".\n"
        f"Its job: {brief}\n\n"
        f"Write approximately {words} words of narration, and no fewer than "
        f"{int(words * 0.8)}. Split it into exactly "
        f"{n_images} consecutive segments; each segment gets its own image prompt. "
        f"Each segment should be roughly {max(20, words // n_images)} words — do not "
        f"write one-line segments.\n\n"
        "Also give this chapter's single most striking figure as key_stat — a bare "
        "number or short quantity and a four-word label. If the chapter has no "
        "meaningful figure, set key_stat to null rather than inventing one.\n\n"
        "Output ONLY a JSON object, no markdown fences:\n"
        '{"key_stat": {"value": "1,415", "label": "men lost with Hood"}, '
        '"segments": [{"narration": "...", "image_prompt": "..."}]}'
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


def select_historical_anchor(candidates, client=None, models=None):
    """Maps a trending subject onto a historical event old enough to be settled.

    Returns {"subject", "topic", "anchor_year", "why"} or None. None is not a
    failure — the caller falls back to the normal topic rotation, which is
    always preferable to publishing something the guardrail could not clear.
    """
    if not candidates:
        return None

    client = client or OpenAI(base_url="https://openrouter.ai/api/v1",
                              api_key=os.getenv("OPENROUTER_API_KEY"))
    models = models or get_free_models()

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

    data = _call(client, models,
                 "You choose historical documentary topics. Output only JSON.",
                 prompt, max_tokens=600)
    if not data or not data.get("topic"):
        print("⚠️ No usable anchor returned.")
        return None

    try:
        year = int(data.get("anchor_year", 9999))
    except (TypeError, ValueError):
        year = 9999

    if year > HISTORY_CUTOFF_YEAR:
        print(f"⚠️ Anchor rejected: year {year} is later than the "
              f"{HISTORY_CUTOFF_YEAR} cutoff. ({data.get('topic', '')[:70]})")
        return None

    if not _anchor_is_relevant(data.get("subject", ""), data.get("topic", ""),
                               data.get("why", "")):
        print("⚠️ Anchor rejected: does not name any actor from the trending "
              "subject, so it is background about the country rather than an "
              "explanation of the story.")
        return None

    print(f"🧭 Trending subject: {data.get('subject', '?')}")
    print(f"   Anchored to {year}: {data['topic'][:100]}")
    return {"subject": data.get("subject", ""), "topic": data["topic"],
            "anchor_year": year, "why": data.get("why", "")}


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
    if enforce_history:
        reason = _violates_history_rule(" ".join(s["narration"] for s in segs))
        if reason:
            print(f"⚠️ Chapter rejected: {reason}.")
            return False
    return True


def _chapter_words(data):
    return sum(len(s["narration"].split()) for s in (data or {}).get("segments", []))


def _validate_chapter(data, expected_words, enforce_history=False):
    """Full check: usable AND long enough."""
    if not _chapter_usable(data, enforce_history):
        return False
    words = _chapter_words(data)
    # Generous band: the arc totals ~1,220 words, and a chapter landing at ~60%
    # is still usable. Below that the pacing collapses.
    if words < expected_words * 0.55:
        print(f"⚠️ Chapter too short: {words} words, wanted ~{expected_words}.")
        return False
    return True


def generate_longform_script(topic: str = None, enforce_history: bool = False) -> dict:
    """Builds the full chaptered script. Returns None only if a chapter is
    unrecoverable — a single weak chapter is retried, not fatal to the run.

    enforce_history is set by the trending lane: it rejects any chapter that
    drifts past the cutoff year or slips into present-tense commentary.
    """
    print("🧠 Long-form LLM engine starting...")

    client = OpenAI(base_url="https://openrouter.ai/api/v1",
                    api_key=os.getenv("OPENROUTER_API_KEY"))
    models = get_free_models()

    if topic is None:
        offset = int(os.getenv("LONGFORM_TOPIC_OFFSET", "-7"))
        topic = pick_topic(offset)
    print(f"📚 Deep dive topic: {topic}")
    if enforce_history:
        print(f"🔒 History rule active: nothing after {HISTORY_CUTOFF_YEAR}.")

    chapters, tail, short_chapters, dropped = [], "", 0, []
    for heading, brief, words in ARC:
        print(f"✍️  Chapter: {heading} (~{words} words)...")
        data, best, feedback = None, None, ""

        # Two attempts, not three: every attempt costs requests from a 50/day
        # budget that eight successful calls already have to fit inside.
        for attempt in range(2):
            try:
                candidate = _call(client, models, SYSTEM_PROMPT,
                                  _chapter_prompt(topic, heading, brief, words,
                                                  tail, feedback),
                                  max_tokens=CHAPTER_MAX_TOKENS)
            except QuotaExhausted as e:
                print(f"🛑 {e}")
                # Nothing further can succeed today, so stop rather than burning
                # the remaining chapters against a wall.
                raise

            if _validate_chapter(candidate, words, enforce_history=enforce_history):
                data = candidate
                break

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
            if attempt < 1:
                print(f"   retrying chapter {heading!r} (2/2)...")

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
            if STYLE_SUFFIX not in s["image_prompt"]:
                s["image_prompt"] = s["image_prompt"].rstrip(". ") + ", " + STYLE_SUFFIX

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

    # Packaging pass: title, description and tags, written against the finished
    # script rather than guessed up front.
    full_text = " ".join(s["narration"] for c in chapters for s in c["scenes"])
    try:
        meta = _packaging_call(client, models, topic, full_text)
    except QuotaExhausted:
        # Six chapters are already written. Losing the video over its title
        # would be absurd — fall back to topic-derived metadata.
        print("🛑 Quota gone before packaging; deriving metadata from the topic.")
        meta = {}

    words = sum(len(s["narration"].split()) for c in chapters for s in c["scenes"])
    title = (meta.get("title") or topic[:70]).strip()
    # Thumbnail text must be short or it turns to mush at 210px wide. If the
    # model ignored that, fall back to the first few words of the title.
    thumb = " ".join((meta.get("thumbnail_text") or "").split()[:5]).strip(" .,:;!?")
    if not thumb or len(thumb) > 42:
        thumb = " ".join(title.split()[:4])

    script = {
        "topic": topic,
        "title": title,
        "description": (meta.get("description") or "").strip(),
        "tags": [t for t in (meta.get("tags") or []) if isinstance(t, str)],
        "thumbnail_text": thumb,
        "thumbnail_badge": str(meta.get("thumbnail_badge") or "").strip()[:12],
        "chapters": chapters,
        "word_count": words,
    }
    note = f", {short_chapters} under target" if short_chapters else ""
    print(f"✅ Script complete: {len(chapters)} chapters, {words} words "
          f"(~{words / 165:.1f} min narrated{note})")
    print(f"📊 Used {_request_count} OpenRouter requests this run.")
    if words < 700:
        print(f"⚠️ {words} words is well under the ~1,220 target — the video will "
              f"run short. Usually means the free models were degraded today.")
    return script


def _packaging_call(client, models, topic, full_text):
    """Title, description, tags and thumbnail text, written against the finished
    script rather than guessed up front."""
    return _call(client, models,
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
                 'Output: {"title": "...", "description": "3 sentences, no hashtags", '
                 '"tags": ["10-14 search tags including proper nouns"], '
                 '"thumbnail_text": "...", "thumbnail_badge": "..."}',
                 max_tokens=700) or {}


def flatten_scenes(script: dict) -> list:
    """Flattens chapters into the scene list the audio/visual engines expect.
    The first scene of each chapter carries card_title, so the visual engine
    renders a chapter card there instead of spending a Pollinations request."""
    scenes = []
    for c_idx, chapter in enumerate(script["chapters"]):
        for s_idx, scene in enumerate(chapter["scenes"]):
            s = dict(scene)
            s["chapter_index"] = c_idx
            s["chapter_heading"] = chapter["heading"]
            if s_idx == 0:
                s["card_title"] = chapter["heading"]
                s["card_number"] = c_idx + 1
                # longform_video reads key_stat off the chapter's first scene.
                s["key_stat"] = chapter.get("key_stat") or {}
            scenes.append(s)
    return scenes


if __name__ == "__main__":
    s = generate_longform_script()
    if s:
        print(json.dumps(s, indent=2)[:2000])
