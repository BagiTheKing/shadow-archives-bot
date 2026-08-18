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
- No ALL CAPS words, no clickbait the script does not deliver. The user message
states the hard character limit; it is a ceiling, not a target.
- Name the CONSEQUENCE, not the event. "X Happened in 1899" states a flat fact and \
does not earn a click.
- UNDERSTANDABLE ON SIGHT. Someone who has never heard of this event must know what \
is at stake from the title alone. A name they do not recognise is not a reason to \
click; what it cost them is.
- Include one searchable proper noun (the ship, the battle, the country, the bridge) \
so the video can also be found by people looking for that subject — but the proper \
noun may never be the ONLY content. The name is for search, the stake is for the \
click, and the title needs both.
  GOOD: "The Math Error That Starved Air Canada Flight 143"
  BAD:  "Dogger Bank Incident 1904" — nobody outside the subject knows what that is.
- BANNED, because they promise significance without specifying anything: "the hidden \
...", "the untold story", "what really happened", "the forgotten ...", "the secret \
...", "you won't believe", "the truth about", "shocking", "insane", "this changed \
everything". If the title would still make sense with the subject swapped out, it is \
one of these.
- VARY THE SHAPE. The user message lists what has already been published. Do not \
return the grammatical shape those titles use. A channel whose every title reads \
"The <thing> That <verb>ed <someone>" has taught its audience that the videos are \
interchangeable, and they stop reading past the third word. Question-led, \
number-led, a plain declarative or a two-part title are all available.

COMMENT QUESTION:
- One question, posted as the first comment under the video. It must be answerable by someone who just watched THIS video and has an opinion — who was actually at fault, which moment was the point of no return, what they would have done. Name the people or the choice; never ask "what do you think?" on its own.
- GOOD: "Nolan carried the order and Lucan read it. Which of them do you blame?"
- BAD:  "Let me know your thoughts in the comments!"

THUMBNAIL:
- thumbnail_text: THREE OR FOUR WORDS ONLY, the most arresting fact in the story, \
readable at 210 pixels wide. Not a sentence, no punctuation. It must not simply \
repeat the title — the two are read together, so they should say different things.
- GOOD: "SUNK IN 20 MINUTES", "THE WRONG VALLEY", "ONE MISSING BOLT".
- BAD:  "The story of how a ship sank", "AMAZING HISTORY FACT".
- thumbnail_badge: the year, or a single number, e.g. "1628".

OUTPUT: Only a valid JSON object, no markdown fences, with this exact shape:
{
  "title": "consequence-first title within the stated character limit, one searchable proper noun",
  "description": "2 sentences summarizing the story + 3 relevant hashtags",
  "thumbnail_text": "three or four words, no punctuation",
  "thumbnail_badge": "the year",
  "comment_question": "one specific question naming someone or something from the story",
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


def _validate(data: dict, title_max: int = 80, avoid_titles=(),
              series_name: str = "") -> bool:
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
    if len(title) > title_max:
        print(f"⚠️ Script rejected: title is {len(title)} chars, "
              f"needs <= {title_max}.")
        return False
    if not validate_title(title, avoid_titles, series_name):
        return False
    return True


# Phrases that promise significance without specifying anything. The test is
# whether the title still makes sense with the subject swapped out: "The Hidden
# WW2 Battle" works equally well for any battle, which is what makes it useless.
BANNED_TITLE_PHRASES = (
    "the hidden", "the untold", "untold story", "what really happened",
    "the forgotten", "the secret", "you won't believe", "you wont believe",
    "the truth about", "shocking", "insane", "changed everything",
    "will blow your mind", "nobody talks about", "no one talks about",
)

# Coarse grammatical shapes. This is not a taxonomy — it exists to answer one
# question: is this the same title the channel has already published fifteen
# times? First match wins, so the order is the specificity order.
_TITLE_SHAPES = (
    ("question-led", r"^(how|why|what|when|who|where)\b"),
    ("number-led", r"^\d"),
    # The channel's house formula: "<something> That <verb>ed <someone>".
    # Both the -ed form and the irregular verbs it actually uses.
    ("x-that-y",
     r"\b(that|who|which)\s+(\w+ed|killed|sank|sunk|lost|cost|broke|burned|"
     r"burnt|destroyed|starved|drowned|froze|blew|tore|ended|began|saved|"
     r"doomed|wrecked|ruined)\b"),
    ("two-part", r"[:—]"),
)

# Above this share of the recent window, a shape has stopped being a choice and
# become a rut. Half is deliberately permissive: the formula is not banned, it
# just cannot be every title.
TITLE_SHAPE_SATURATION = 0.5
TITLE_SHAPE_WINDOW = 8
# Under this many known titles there is no rut to detect, and rejecting on a
# sample of two would just be noise.
TITLE_SHAPE_MIN_HISTORY = 4


def strip_series_marker(title: str, series_name: str = "") -> str:
    """The title as a viewer reads it, without the series marker or hashtags.

    Needed at both ends: the marker was a prefix for the first fifty videos and
    is a suffix from here on, and the channel's history contains both.
    """
    # Hashtags first, and the order is load-bearing: "#51" in a suffix marker
    # matches the hashtag pattern too, so stripping tags afterwards would eat
    # the episode number and leave the series name stranded on the end.
    text = re.sub(r"(\s*#\w+)+\s*$", "", (title or "").strip())
    if series_name:
        marker = rf"{re.escape(series_name)}\s*#?\s*\d*"
        text = re.sub(rf"^\s*{marker}\s*[—–-]\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(rf"\s*[—–-]\s*{marker}\s*$", "", text, flags=re.IGNORECASE)
    return text.strip(" —–-")


def title_shape(title: str) -> str:
    text = (title or "").strip()
    for name, pattern in _TITLE_SHAPES:
        if re.search(pattern, text, re.IGNORECASE):
            return name
    return "declarative"


def _shape_is_saturated(shape: str, avoid_titles, series_name: str = "") -> bool:
    recent = [t for t in (avoid_titles or []) if t][:TITLE_SHAPE_WINDOW]
    if len(recent) < TITLE_SHAPE_MIN_HISTORY:
        return False
    same = sum(1 for t in recent
               if title_shape(strip_series_marker(t, series_name)) == shape)
    return same / len(recent) > TITLE_SHAPE_SATURATION


def _shape_warning(recent, series_name: str = "") -> str:
    """Names the rut, for the writer that is about to fall into it.

    Listing the recent titles is not enough on its own — a model reads fifteen
    examples of one sentence shape as the house style and produces a sixteenth.
    """
    window = [t for t in (recent or []) if t][:TITLE_SHAPE_WINDOW]
    if len(window) < TITLE_SHAPE_MIN_HISTORY:
        return ""
    counts = {}
    for t in window:
        shape = title_shape(strip_series_marker(t, series_name))
        counts[shape] = counts.get(shape, 0) + 1
    shape, n = max(counts.items(), key=lambda kv: kv[1])
    if n / len(window) <= TITLE_SHAPE_SATURATION:
        return ""
    return (f"WARNING: {n} of the last {len(window)} titles use the same "
            f"grammatical shape. Do NOT write another one in that shape — it is "
            "why this channel's titles are no longer being read. Pick a "
            "different construction.\n")


def validate_title(title: str, avoid_titles=(), series_name: str = "") -> bool:
    """The backstop behind the prompt's title rules.

    The prompt used to instruct the formula outright — "titles that perform on
    this channel follow the shape 'the <decision> that <destroyed something>'"
    — with three worked examples of it, which is why all fifty published titles
    are the same sentence with the nouns changed. The instruction is gone; this
    is what stops it coming back by habit.
    """
    text = (title or "").strip()
    if not text:
        return False

    low = text.lower()
    for phrase in BANNED_TITLE_PHRASES:
        if phrase in low:
            print(f"⚠️ Title rejected: {phrase!r} promises significance without "
                  f"naming anything. ({text!r})")
            return False

    shape = title_shape(text)
    if _shape_is_saturated(shape, avoid_titles, series_name):
        print(f"⚠️ Title rejected: shape {shape!r} is most of the last "
              f"{TITLE_SHAPE_WINDOW} titles. ({text!r})")
        return False

    return True


# Words that carry no meaning on a thumbnail. Used only to build the fallback
# headline, never to edit one the writer actually supplied.
_THUMB_FILLER = {"the", "a", "an", "of", "that", "which", "who", "and", "but",
                 "to", "in", "on", "at", "for", "by", "with", "from", "its",
                 "his", "her", "their", "was", "were", "is", "it"}


def normalize_thumbnail_fields(data: dict) -> dict:
    """Fills thumbnail_text/thumbnail_badge, in place.

    Same contract as the long-form writer, which has produced these fields since
    the thumbnail engine was written. The Shorts writer is being asked for them
    for the first time, so every path has to survive their absence: a model that
    ignores the new instruction must still yield a publishable script.
    """
    # A writer that returned a sentence ignored the instruction, and the first
    # five words of a sentence are not a headline — they are a fragment. Treat
    # an over-long reply as no reply and draw from the title instead.
    supplied = (data.get("thumbnail_text") or "").strip(" .,:;!?\"'")
    thumb = supplied if len(supplied.split()) <= 5 else ""
    if not thumb or len(thumb) > 42:
        # Falling back to the title's opening words gives "The Mistake That
        # Killed" — four words of grammar and no fact. Dropping the filler first
        # keeps the words a viewer actually reads at 210px wide.
        title = (data.get("title") or "").strip()
        words = [w for w in re.sub(r"[^\w\s'-]", "", title).split()
                 if w.lower() not in _THUMB_FILLER]
        thumb = " ".join((words or title.split())[:4])
    data["thumbnail_text"] = thumb

    badge = str(data.get("thumbnail_badge") or "").strip()[:12]
    if not badge:
        # A year out of the topic line is the badge the long-form videos use,
        # and the topic pool states one for almost every entry.
        year = re.search(r"\b(1[0-9]{3}|20[0-9]{2})\b", data.get("topic") or "")
        badge = year.group(0) if year else ""
    data["thumbnail_badge"] = badge
    return data


def generate_video_script(topic: str = None, avoid_titles=(),
                         title_max: int = 80, max_retries_per_model: int = 2,
                         series_name: str = ""):
    """Generates the daily script.

    `topic` is chosen by the caller now, because choosing it well needs the
    channel's published history and this module has no business calling the
    YouTube API. Passing None keeps the old behaviour — the bare date rotation,
    which is what the fallback path wants.

    `avoid_titles` are the channel's most recent titles. They are shown to the
    writer so it cannot answer a topic it has already covered with a reworded
    version of the same title, which is the exact shape the duplicates took.
    """
    print("🧠 Initializing LLM Engine...")

    # Same writer as the long-form lanes: Gemini's free tier when a key is
    # present, OpenRouter's free models otherwise.
    from llm_provider import get_pool, describe_provider
    pool = get_pool()
    print(f"🤖 Writer: {describe_provider()}")

    if topic is None:
        topic = pick_topic()
    if not topic:
        print("❌ No topic available.")
        return None
    print(f"📚 Today's topic: {topic}")

    avoid_block = ""
    recent = [t for t in (avoid_titles or []) if t][:30]
    if recent:
        listed = "\n".join(f"- {t}" for t in recent)
        avoid_block = (
            "\nALREADY PUBLISHED ON THIS CHANNEL — these stories are used up:\n"
            f"{listed}\n"
            "If the story you were given is the same event as any of these, say so "
            'by returning {"duplicate": true} and nothing else. Otherwise write a '
            "title that could not be mistaken for one of them.\n"
            f"{_shape_warning(recent, series_name)}"
        )

    user_prompt = (
        f"Write today's YouTube Short script about this specific true story:\n\n{topic}\n\n"
        "Stick to established facts about this event. "
        "Lead with the decision or error that caused the disaster — that is the point "
        "of this channel, not the event in general.\n"
        f"{avoid_block}"
        "CRITICAL: the total narration across all 5 scenes combined must be "
        "130-150 words. Scripts under 120 words will be rejected.\n"
        "CRITICAL: scene 1 must be at most 12 words, must not begin with setup or a "
        "date, and must contain a number, a name, or a superlative. A scene 1 that "
        "reads as an introduction will be rejected.\n"
        f"CRITICAL: the title must be at most {title_max} characters, including "
        "spaces. A longer title will be rejected.\n"
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
                if data.get("duplicate") and not data.get("scenes"):
                    # The writer recognised the topic as one already published.
                    # Trust it: the caller's exclusion list missed this one.
                    print("🚫 Writer reports this story is already published.")
                    return None
                if _validate(data, title_max, avoid_titles, series_name):
                    data["topic"] = topic
                    normalize_thumbnail_fields(data)
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
