import os
import json
import re
import time

from openai import OpenAI
from topics import pick_topic

# "openrouter/free" is not a stable model ID. Use an explicit fallback chain of
# known free models instead — if one is offline or rate-limited, the next is tried.
# Check https://openrouter.ai/models?max_price=0 occasionally and refresh this list.
import requests

def get_free_models(limit: int = 6) -> list:
    """Fetch currently-available free models live, so the list never goes stale."""
    models = ["openrouter/free"]  # OpenRouter's own free-model router, try it first
    try:
        r = requests.get("https://openrouter.ai/api/v1/models", timeout=20)
        r.raise_for_status()
        for m in r.json().get("data", []):
            bad = ("safety", "guard", "code", "coder", "laguna",
                   "math", "vision", "embed", "moderation")
            if any(k in m["id"].lower() for k in bad):
                continue
            p = m.get("pricing", {})
            if (m["id"].endswith(":free")
                    and float(p.get("prompt", 1)) == 0
                    and float(p.get("completion", 1)) == 0):
                models.append(m["id"])
                if len(models) > limit:
                    break
    except Exception as e:
        print(f"⚠️ Could not fetch live model list ({e}), using router only.")
    return models

SYSTEM_PROMPT = """You are a script writer for The Archive Paradox, a YouTube Shorts channel about \
catastrophic decisions: one order, one design flaw, one miscalculation, and the disaster it caused.
You write about REAL, verifiable historical events. Never invent facts, dates, or numbers.

THE HOOK (scene 1) DECIDES WHETHER THE VIDEO SURVIVES. Viewers leave in under two \
seconds, so scene 1 is judged by a stricter standard than everything else:
- Maximum 12 words. It must be speakable in under three seconds.
- It must state the anomaly immediately: a hard number, a contradiction, or a named \
consequence. The viewer should have a question they need answered by word ten.
- NEVER open with setup, date-first framing, or a question to the audience. Banned \
openers: "In", "In the year", "Back in", "It was", "Imagine", "This is", "Picture this", \
"Have you ever", "Most people", "What if", "Nobody knows".
- GOOD: "Sweden's mightiest warship sank twenty minutes into its first voyage."
- GOOD: "Six hundred cavalrymen charged the wrong valley because of one word."
- BAD:  "In 1628, Sweden launched a warship called the Vasa."
- BAD:  "Have you ever wondered how a single order could kill hundreds?"

STRUCTURE (non-negotiable, this is what drives retention):
- Scene 1: the HOOK, per the rules above.
- Scene 2: name the decision or the error. Be specific about who chose what.
- Scenes 3-4: escalate. Each scene must end on a mini-cliffhanger or a turn.
- Scene 5: the cost, in numbers, then ONE final line that lingers (dark irony or the \
detail that makes it worse). Do not end on a moral or a lesson.
- Total spoken length: 130-150 words (about 55 seconds of narration). 5 scenes.

WRITING RULES:
- Short punchy sentences. Max 15 words per sentence.
- Concrete specifics (numbers, names, dates) beat vague drama.
- BANNED phrases: "little did they know", "but that's not all", "what happened next \
will shock you", "to this day", "lost to history", "the rest is history".
- Write for the ear, not the page: no parentheses, no semicolons.

IMAGE PROMPTS:
- Each scene gets one image_prompt describing a SINGLE clear focal subject \
(one person, one object, one place). Crowded prompts produce muddy AI images.
- Vary the shot types across scenes: extreme close-up, wide establishing shot, \
low-angle, overhead, silhouette. State the shot type first.
- Every prompt must end with exactly: "dramatic chiaroscuro lighting, dark moody \
cinematic oil painting, rich texture, muted color palette with one accent color, \
highly detailed, no text, no watermark"

TITLE RULES (the title is the second filter, after the hook):
- Under 80 characters, no ALL CAPS words, no clickbait the script does not deliver.
- Name the CONSEQUENCE, not the event. Titles that perform on this channel follow the \
shape "the <decision> that <destroyed something>":
  "The Mistake That Killed the Light Brigade", "The Order That Killed Six Hundred", \
"Scotland's Colonial Gamble That Destroyed a Nation".
- Titles that state a flat fact do not perform. Avoid "X Happened in 1899" shapes.
- Include one searchable proper noun (the ship, the battle, the country, the bridge) so \
the video can also be found by people looking for that subject.

OUTPUT: Only a valid JSON object, no markdown fences, with this exact shape:
{
  "title": "consequence-first title under 80 chars, one searchable proper noun",
  "description": "2 sentences summarizing the story + 3 relevant hashtags",
  "tags": ["8-12 specific search tags, including the proper nouns from the story"],
  "scenes": [
    {"narration": "...", "image_prompt": "..."}
  ]
}"""


def _extract_json(raw: str):
    """Models sometimes wrap JSON in fences or add commentary. Pull out the object."""
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    # Last resort: grab the outermost braces
    if not raw.startswith("{"):
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end != -1:
            raw = raw[start : end + 1]
    return json.loads(raw, strict=False)


# Openers that turn the first two seconds into setup instead of a hook. The model
# is told to avoid these; this is the backstop, because a weak hook is the single
# most expensive thing that can reach the channel.
BANNED_HOOK_OPENERS = (
    "in the year", "in 1", "in 2", "back in", "it was", "imagine",
    "this is", "picture this", "have you ever", "most people",
    "what if", "nobody knows", "there was a", "once upon",
)

HOOK_MAX_WORDS = 14  # prompt asks for 12; allow slight slack before rejecting

# Words that are capitalised only because they start the sentence. Anything else
# capitalised in first position is a real name ("Austria's army attacked itself").
SENTENCE_OPENERS = {
    "a", "an", "the", "this", "that", "these", "those", "his", "her", "their",
    "its", "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "twenty", "hundreds", "thousands", "no", "not", "every",
    "when", "how", "why", "who", "after", "before", "during", "within",
    "he", "she", "they", "it", "we", "you", "there", "here", "some", "all",
}


def _has_proper_noun(hook: str) -> bool:
    """True if the hook names something. A capitalised word counts, except in
    first position where the capital may just be sentence case."""
    words = hook.split()
    for i, raw in enumerate(words):
        word = raw.strip("\"'“”‘’.,;:!?()[]")
        if len(word) < 3 or not word[0].isupper():
            continue
        if i == 0 and word.split("'")[0].lower() in SENTENCE_OPENERS:
            continue
        return True
    return False


def _validate_hook(hook: str) -> bool:
    h = hook.strip()
    low = h.lower()

    words = len(h.split())
    if words > HOOK_MAX_WORDS:
        print(f"⚠️ Hook rejected: {words} words, needs <= {HOOK_MAX_WORDS}. ({h!r})")
        return False

    for opener in BANNED_HOOK_OPENERS:
        if low.startswith(opener):
            print(f"⚠️ Hook rejected: opens with setup {opener!r}. ({h!r})")
            return False

    # A hook earns attention with something concrete: a number, a proper noun, or a
    # superlative. Purely abstract openers ("A terrible mistake was made") do not.
    # Narration is written for the ear, so quantities often appear as words
    # ("six hundred cavalrymen", "lost hundreds") rather than digits. Both count,
    # and so do the plurals.
    has_number = bool(re.search(
        r"\d|\b(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
        r"twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundreds?|"
        r"thousands?|millions?|billions?|dozens?)\b", low))
    has_proper_noun = _has_proper_noun(h)
    has_superlative = bool(re.search(
        r"\b(most|largest|worst|deadliest|first|only|mightiest|greatest|never)\b", low))
    if not (has_number or has_proper_noun or has_superlative):
        print(f"⚠️ Hook rejected: no number, name or superlative. ({h!r})")
        return False

    return True


def _validate(data: dict) -> bool:
    scenes = data.get("scenes")
    if not isinstance(scenes, list) or not (4 <= len(scenes) <= 6):
        return False
    for s in scenes:
        if not s.get("narration") or not s.get("image_prompt"):
            return False
    total_words = sum(len(s["narration"].split()) for s in scenes)
    if not (90 <= total_words <= 190):  # keep the short in the 40-70s zone
        print(f"⚠️ Script rejected: {total_words} words (out of range).")
        return False
    if not _validate_hook(scenes[0]["narration"]):
        return False

    title = (data.get("title") or "").strip()
    if len(title) > 80:
        print(f"⚠️ Script rejected: title is {len(title)} chars, needs <= 80.")
        return False
    return True


def generate_video_script(max_retries_per_model: int = 2):
    """Generates the daily script. Rotates topic by date, falls through free models."""
    print("🧠 Initializing LLM Engine...")

    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=os.getenv("OPENROUTER_API_KEY"),
    )

    topic = pick_topic()
    print(f"📚 Today's topic: {topic}")

    user_prompt = (
        f"Write today's YouTube Short script about this specific true story:\n\n{topic}\n\n"
        "Stick to established facts about this event. "
        "Lead with the decision or error that caused the disaster — that is the point "
        "of this channel, not the event in general.\n"
        "CRITICAL: the total narration across all 5 scenes combined must be "
        "130-150 words. Scripts under 120 words will be rejected.\n"
        "CRITICAL: scene 1 must be at most 12 words, must not begin with setup or a "
        "date, and must contain a number, a name, or a superlative. A scene 1 that "
        "reads as an introduction will be rejected.\n"
        "Output ONLY the JSON object."
    )

    for model in get_free_models():
        for attempt in range(1, max_retries_per_model + 1):
            print(f"🤖 Trying {model} (attempt {attempt}/{max_retries_per_model})...")
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.8,
                    max_tokens=4000,
                )
                content = response.choices[0].message.content
                if not content or not content.strip():
                    raise ValueError("model returned empty content")
                data = _extract_json(content)
                if _validate(data):
                    data["topic"] = topic
                    print(f"✅ Script generated by {response.model}")
                    return data
                print("⚠️ Script failed validation, retrying...")
            except Exception as e:
                print(f"⚠️ {model} failed: {e}")
                time.sleep(15 if "429" in str(e) else 2)

    print("❌ All models failed.")
    return None


if __name__ == "__main__":
    script = generate_video_script()
    if script:
        print(json.dumps(script, indent=2))
