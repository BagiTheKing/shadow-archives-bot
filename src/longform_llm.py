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
        "Output ONLY a JSON object, no markdown fences:\n"
        '{"segments": [{"narration": "...", "image_prompt": "..."}]}'
    )


def _validate_chapter(data, expected_words):
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
    return True


def generate_longform_script(topic: str = None) -> dict:
    """Builds the full chaptered script. Returns None only if a chapter is
    unrecoverable — a single weak chapter is retried, not fatal to the run."""
    print("🧠 Long-form LLM engine starting...")

    client = OpenAI(base_url="https://openrouter.ai/api/v1",
                    api_key=os.getenv("OPENROUTER_API_KEY"))
    models = get_free_models()

    if topic is None:
        offset = int(os.getenv("LONGFORM_TOPIC_OFFSET", "-7"))
        topic = pick_topic(offset)
    print(f"📚 Deep dive topic: {topic}")

    chapters, tail = [], ""
    for heading, brief, words in ARC:
        print(f"✍️  Chapter: {heading} (~{words} words)...")
        data = None
        for attempt in range(2):
            data = _call(client, models, SYSTEM_PROMPT,
                         _chapter_prompt(topic, heading, brief, words, tail),
                         max_tokens=2000)
            if _validate_chapter(data, words):
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
        chapters.append({"heading": heading, "scenes": segs})
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
                 'Output: {"title": "...", "description": "3 sentences, no hashtags", '
                 '"tags": ["10-14 search tags including proper nouns"]}',
                 max_tokens=700) or {}

    words = sum(len(s["narration"].split()) for c in chapters for s in c["scenes"])
    script = {
        "topic": topic,
        "title": (meta.get("title") or topic[:70]).strip(),
        "description": (meta.get("description") or "").strip(),
        "tags": [t for t in (meta.get("tags") or []) if isinstance(t, str)],
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
            scenes.append(s)
    return scenes


if __name__ == "__main__":
    s = generate_longform_script()
    if s:
        print(json.dumps(s, indent=2)[:2000])
