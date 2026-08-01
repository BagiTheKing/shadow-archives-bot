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
    ("Cold Open",
     "State the anomaly in the first two sentences, the same way a Short would open. "
     "Then promise the question this video answers: not what happened, but how the "
     "decision that caused it looked reasonable at the time. Do not summarise the ending.",
     110),
    ("What Was Supposed To Happen",
     "Establish the plan as its designers understood it. The viewer should finish this "
     "chapter thinking the plan sounds sensible. Give the specifics: who, when, what "
     "resources, what the expected outcome was.",
     200),
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
     190),
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


def _call(client, models, system, user, max_tokens, retries=2):
    """Runs a prompt down the free-model fallback chain. Returns parsed JSON."""
    for model in models:
        for attempt in range(1, retries + 1):
            try:
                r = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "system", "content": system},
                              {"role": "user", "content": user}],
                    temperature=0.7,
                    max_tokens=max_tokens,
                )
                content = r.choices[0].message.content
                if not content or not content.strip():
                    raise ValueError("empty content")
                return _extract_json(content)
            except Exception as e:
                print(f"⚠️ {model} failed: {e}")
                time.sleep(15 if "429" in str(e) else 2)
    return None


def _chapter_prompt(topic, heading, brief, words, previous_tail):
    n_images = max(2, round(words / WORDS_PER_IMAGE))
    continuity = (f"\nThe previous chapter ended with: \"{previous_tail}\"\n"
                  "Continue from there without repeating it.\n" if previous_tail else "")
    return (
        f"Story: {topic}\n"
        f"{continuity}\n"
        f"Write the chapter titled \"{heading}\".\n"
        f"Its job: {brief}\n\n"
        f"Write approximately {words} words of narration. Split it into exactly "
        f"{n_images} consecutive segments; each segment gets its own image prompt.\n\n"
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


def _validate_chapter(data, expected_words, enforce_history=False):
    segs = (data or {}).get("segments")
    if not isinstance(segs, list) or not segs:
        return False
    for s in segs:
        if not s.get("narration") or not s.get("image_prompt"):
            return False
    words = sum(len(s["narration"].split()) for s in segs)
    # Generous band: the arc totals ~1,220 words, and a chapter landing at 60% is
    # still usable. Below that the pacing collapses.
    if words < expected_words * 0.55:
        print(f"⚠️ Chapter too short: {words} words, wanted ~{expected_words}.")
        return False

    if enforce_history:
        joined = " ".join(s["narration"] for s in segs)
        reason = _violates_history_rule(joined)
        if reason:
            print(f"⚠️ Chapter rejected: {reason}.")
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

    chapters, tail = [], ""
    for heading, brief, words in ARC:
        print(f"✍️  Chapter: {heading} (~{words} words)...")
        data = None
        for attempt in range(2):
            data = _call(client, models, SYSTEM_PROMPT,
                         _chapter_prompt(topic, heading, brief, words, tail),
                         max_tokens=2000)
            if _validate_chapter(data, words, enforce_history=enforce_history):
                break
            print(f"   retrying chapter {heading!r} ({attempt + 1}/2)...")
            data = None

        if not data:
            print(f"❌ Chapter {heading!r} could not be generated.")
            return None

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

    # Packaging pass: title, description and tags, written against the finished
    # script rather than guessed up front.
    full_text = " ".join(s["narration"] for c in chapters for s in c["scenes"])
    meta = _call(client, models,
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
    print(f"✅ Script complete: {len(chapters)} chapters, {words} words "
          f"(~{words / 165:.1f} min narrated)")
    return script


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
