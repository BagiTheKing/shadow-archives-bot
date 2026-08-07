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
- ONE FIGURE AT MOST, and commit to a distance: one face filling the frame, one \
silhouette, one object, or a landscape. Two or three half-size people in one \
frame render as smudges with broken hands. Never make hands the subject.
- ANCHOR THE ERA: name the period's dress, ships, weapons and buildings \
explicitly so nothing modern appears in a historical scene.
- Every prompt must end with exactly: "dramatic chiaroscuro lighting, dark moody \
cinematic oil painting, visible expressive brushwork, rich texture, muted color \
palette with one accent color, highly detailed, no text, no watermark"

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


def _strip_fences(raw: str) -> str:
    """Removes markdown fences, wherever they are.

    Reasoning models often narrate before the JSON ("Okay, the user wants...")
    and then fence the object, so anchoring the fence pattern to the start of
    the string — as this used to — misses it entirely.
    """
    fenced = re.search(r"```(?:json|JSON)?\s*(.+?)\s*```", raw, re.DOTALL)
    if fenced:
        return fenced.group(1).strip()
    # An opening fence with no closing one means the reply was cut off mid-object.
    raw = re.sub(r"^\s*```(?:json|JSON)?\s*", "", raw)
    return re.sub(r"\s*```\s*$", "", raw).strip()


def _outermost_object(raw: str) -> str:
    """The JSON object inside surrounding prose. Tolerates a missing closing
    brace, which is what a truncated reply looks like."""
    start = raw.find("{")
    if start == -1:
        return raw
    end = raw.rfind("}")
    return raw[start:end + 1] if end > start else raw[start:]


def _drop_trailing_commas(raw: str) -> str:
    return re.sub(r",\s*([}\]])", r"\1", raw)


def _close_containers(prefix: str):
    """Appends the brackets needed to close `prefix`, or None if it ends inside
    a string literal (in which case the caller should cut back further)."""
    stack, in_string, escaped = [], False, False
    for ch in prefix:
        if escaped:
            escaped = False
        elif in_string:
            if ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch in "[{":
            stack.append(ch)
        elif ch == "]" and stack and stack[-1] == "[":
            stack.pop()
        elif ch == "}" and stack and stack[-1] == "{":
            stack.pop()
    if in_string or not stack:
        return None
    return prefix + "".join("]" if c == "[" else "}" for c in reversed(stack))


def _salvage_truncated(raw: str):
    """Recovers as much of a cut-off object as is still well-formed.

    A free model that runs out of output tokens mid-chapter leaves something
    like `{"segments": [{"narration": "...", "image_prompt": "wide sh` —
    unparseable, but the three complete segments before it are perfectly good
    narration. Rather than throw the chapter away, cut back to the last element
    that finished and close the containers that are still open.

    Returns the parsed object, or None if nothing usable survives.
    """
    # First try closing what is open without cutting anything: covers a reply
    # that stopped on a clean boundary, e.g. right after a comma.
    for candidate in (raw, _drop_trailing_commas(raw.rstrip().rstrip(","))):
        closed = _close_containers(candidate)
        if closed:
            try:
                return json.loads(closed, strict=False)
            except ValueError:
                pass

    # Then walk back through completed values, newest first.
    for i in range(len(raw) - 1, 0, -1):
        if raw[i] not in "}]":
            continue
        closed = _close_containers(_drop_trailing_commas(raw[:i + 1]))
        if not closed:
            continue
        try:
            return json.loads(closed, strict=False)
        except ValueError:
            continue
    return None


def _extract_json(raw: str):
    """Pulls a JSON object out of a model reply, repairing the damage these
    models actually produce.

    The strict path handles well-behaved replies. Everything after it exists
    because free models return fenced JSON wrapped in commentary, trailing
    commas, smart quotes where straight ones belong, and — most often — output
    that simply stops mid-sentence when it hits the token ceiling. Each stage
    is tried in order of how much it assumes, so a clean reply is never
    "repaired" into something different from what the model said.

    Raises the original decode error if nothing works, so the caller can still
    tell a parse failure from a refusal.
    """
    if not raw or not raw.strip():
        raise ValueError("empty content")

    cleaned = _outermost_object(_strip_fences(raw.strip()))

    attempts = [
        cleaned,
        _drop_trailing_commas(cleaned),
        # Last resort: a model that used typographic quotes as JSON delimiters.
        # Tried last because it corrupts curly quotes used legitimately inside
        # narration, and only helps when the earlier stages have all failed.
        _drop_trailing_commas(
            cleaned.replace("“", '"').replace("”", '"')
                   .replace("‘", "'").replace("’", "'")),
    ]

    first_error = None
    for candidate in attempts:
        try:
            return json.loads(candidate, strict=False)
        except ValueError as e:
            first_error = first_error or e

    for candidate in attempts:
        salvaged = _salvage_truncated(candidate)
        if salvaged is not None:
            print("   🩹 Repaired a truncated JSON reply.")
            return salvaged

    raise first_error


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

    # Same writer as the long-form lanes: Gemini's free tier when a key is
    # present, OpenRouter's free models otherwise.
    from llm_provider import get_pool, describe_provider
    pool = get_pool()
    print(f"🤖 Writer: {describe_provider()}")

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

    for model in pool.models:
        client = pool.client_for(model)
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
