"""
Long-form script engine — chaptered long-form helper utilities and robust JSON extraction.
"""

import os
import json
import re
import time

from openai import OpenAI

from llm_engine import get_free_models, _extract_json
from topics import pick_topic

# Words per chapter. edge-tts at +8% rate lands around 165 wpm, so ~1,200 words
# is roughly 7.5 minutes — inside the 6-9 minute target.
ARC = [
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


def _order_models(models):
    """Preferred model first, then the rest, capped so one call cannot walk the
    entire list."""
    if _preferred_model and _preferred_model in models:
        rest = [m for m in models if m != _preferred_model]
        ordered = [_preferred_model] + rest
    else:
        ordered = list(models)
    return ordered[:MAX_MODELS_PER_CALL]


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
            content = getattr(r.choices[0].message, 'content', None)
            if not content or not content.strip():
                raise ValueError("empty content")
            # Try to extract JSON; on parse failure, save raw output and clear
            # preferred model so other models are tried first on the next call.
            try:
                data = _extract_json(content)
            except Exception as parse_e:
                try:
                    os.makedirs("build/llm_failures", exist_ok=True)
                    fn = os.path.join("build/llm_failures",
                                      f"{model.replace('/', '_')}-{int(time.time())}.txt")
                    with open(fn, "w", encoding="utf-8") as f:
                        f.write("=== RAW MODEL OUTPUT ===\n")
                        f.write(content or "<empty>\n")
                        f.write("\n\n=== PARSE ERROR ===\n")
                        f.write(repr(parse_e))
                except Exception:
                    pass
                if _preferred_model == model:
                    print(f"   ⚠️ Clearing preferred model {model} due to parse errors.")
                    _preferred_model = None
                raise

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
            if "429" in err:
                time.sleep(10)          # per-minute throttle, worth one pause
    return None
