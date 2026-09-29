import os
import json
import re
import time

from openai import OpenAI

import formats
from topics import pick_topic, format_of as topics_format_of

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

# Whether hard-sell / sensational language is allowed in narration, hooks, handoffs
# and titles. Defaults on: 67 videos in 2 months returned 27 subscribers and 10k
# views, with a measured overall thumbnail CTR of 1.67% — well under the ~4%
# floor for a small channel — so the restrained voice is the thing on trial here,
# not the thing defended by default. Set CLICKBAIT_MODE=off to fall back to the
# original restrained phrasing (the "BANNED phrases" lists below) if the test goes
# badly and needs reverting without hunting the lists back down.
#
# This does NOT touch factual accuracy. "Never invent facts, dates, numbers or
# quotes" stays enforced regardless of this flag — that rule protects against
# misinformation strikes and is not what was being tested. What this flag
# controls is marketing language: title framing, hooks, teases, sensational
# phrasing. A title can ask a dramatic question the video then answers; the
# narration itself still can't assert something the record doesn't support.
CLICKBAIT_MODE = os.getenv("CLICKBAIT_MODE", "on").strip().lower() != "off"

# Reinstates the original restraint when CLICKBAIT_MODE=off. Kept as an addendum
# rather than deleted so reverting the experiment is a one-line env var flip, not
# an archaeology project through git history.
_RESTRAINED_ADDENDUM = """

RESTRAINED MODE IS ACTIVE (CLICKBAIT_MODE=off) — the following are additionally banned:
- Narration phrases: "little did they know", "but that's not all", "what happened \
next will shock you", "to this day", "lost to history", "the rest is history".
- Title phrases: "the hidden", "the untold", "untold story", "what really happened", \
"the forgotten", "the secret", "you won't believe", "you wont believe", "truth about", \
"will blow your mind", "nobody talks about", "no one talks about"."""


def get_system_prompt() -> str:
    """SYSTEM_PROMPT, with the restrained-language addendum only when the
    hard-sell experiment is explicitly switched off."""
    return SYSTEM_PROMPT if CLICKBAIT_MODE else SYSTEM_PROMPT + _RESTRAINED_ADDENDUM


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
- THE TURN: once, in scene 3 or 4, just before the moment everything goes wrong, \
the narrator speaks straight to the viewer in one short line (at most 8 words): \
"Now watch what the captain does next.", "Remember that order?", "Here is the \
part nobody planned for." It must point at something that really happens in \
the next line. Once only, never in the hook, never a fake cliffhanger.
- Total spoken length: 110-130 words (about 45 seconds of narration — the pipeline \
adds a short closing tease after your last line). 5 scenes.

CLARITY (violating this is why viewers watch to the end and still feel nothing):
- Write for someone who has NEVER heard of this event and knows nothing about \
the period. Every scene must be understood on ONE listen at speaking speed — \
there is no rewinding in a Shorts feed.
- No jargon. Any military, engineering or period term must be explained in plain \
words in the same sentence it appears, or replaced. "The magazine" is where the \
shells are stored; "a salvo" is a round of gunfire; if a term needs a definition \
you cannot afford, cut it.
- ONE storyline, no digressions. At most two named people; call everyone else by \
their role ("the captain", "the engineers").
- The story must turn on ONE concrete MECHANISM — the specific thing that went \
wrong, stated so plainly a viewer could repeat it to a friend afterwards. "They \
ran out of fuel because someone converted pounds to kilograms wrong" is a \
mechanism. "The plan failed" and "winter came" are summaries, not mechanisms.
- Include at least ONE specific verifiable detail that even someone who already \
knows this story probably does not know. The famous outline is not the product; \
that detail is.
- The test: could a 12-year-old retell the whole story after one viewing? If \
not, simplify until they could.

WRITING RULES:
- Short punchy sentences. Max 15 words per sentence.
- Concrete specifics (numbers, names, dates) beat vague drama.
- Write for the ear, not the page: no parentheses, no semicolons.

SHOTS (the pictures): one per SENTENCE of the narration, in order.
- Each scene has a "shots" list with exactly one entry per sentence of that \
scene's narration. The picture changes when the sentence starts, so each \
image_prompt must show exactly what THAT sentence says: the ship, the person, \
the place, the object or the moment it names. A viewer with the sound off must \
be able to follow the story from the pictures alone.
- Scene 1's picture shows the story's subject itself, recognisably (the \
warship, the burning telegraph office, the bridge), never a generic mood shot.
- Never the same subject twice in a row, never the same picture twice in the \
video. Vary the distance across the video: wide establishing shot, medium \
shot, extreme close-up of an object, overhead, silhouette. State the shot \
type first.
- One clear focal subject per picture (one person, one object, one place). \
ONE FIGURE AT MOST, and commit to a distance. Never make hands the subject.
- ANCHOR THE ERA: name the period's dress, ships, weapons and buildings \
explicitly so nothing modern appears in a historical scene.
- Every image_prompt must end with exactly: "dramatic chiaroscuro lighting, \
dark moody cinematic oil painting, visible expressive brushwork, rich texture, \
muted color palette with one accent color, highly detailed, no text, no \
watermark"
- archive_query: when the sentence names something that has a real \
photograph or period artwork (the ship, the person, the battle, the building, \
the instrument), 2-4 words to search Wikimedia Commons with, naming that \
exact subject ("Vasa ship", "Richard Carrington", "Tay Bridge"). No dates, no \
generic words. Otherwise "". A real picture found this way is used instead of \
the painting, so aim for it wherever the sentence allows.

TITLE RULES (the title is the second filter, after the hook):
- No ALL CAPS words. The user message states the hard character limit; it is a
ceiling, not a target.
- Name the CONSEQUENCE, not the event. "X Happened in 1899" states a flat fact and \
does not earn a click.
- UNDERSTANDABLE ON SIGHT. Someone who has never heard of this event must know what \
is at stake from the title alone. A name they do not recognise is not a reason to \
click; what it cost them is.
- IF THE ACTOR IS A PERSON, DO NOT NAME THEM UNLESS THE NAME ITSELF IS FAMOUS. Test: \
would someone with zero history knowledge recognise this name on sight? "Hitler" or \
"Einstein" pass; almost every other historical figure fails, including famous-to-\
history-buffs names like generals, ministers or scientists. When it fails, replace \
the name with the plainest role that IS instantly recognisable on its own — "A Nazi \
War Criminal", "A U.S. President's Doctor", "A Soviet Engineer" — and let the video \
reveal who they actually were. This role swap IS the curiosity gap; do not also \
spend it on a name nobody recognises. The consequence clause must be plain outcome \
language, not historical shorthand that requires already knowing the story ("the \
Nuremberg gallows", "the Munich Agreement") — say literally what happened.
  GOOD: "A Nazi War Criminal Died Just 2 Hours Before His Execution"
  BAD:  "Hermann Göring Cheated the Nuremberg Gallows by Two Hours" — the name is \
recognised only by history buffs, and "the Nuremberg gallows" requires already \
knowing what Nuremberg was.
- Include one searchable proper noun (the ship, the battle, the country, the bridge) \
so the video can also be found by people looking for that subject — but the proper \
noun may never be the ONLY content. The name is for search, the stake is for the \
click, and the title needs both.
  GOOD: "The Math Error That Starved Air Canada Flight 143"
  BAD:  "Dogger Bank Incident 1904" — nobody outside the subject knows what that is.
- SELL IT. This is a title competing in a feed, not a museum caption. A hard \
number, a superlative, a stake, a curiosity gap, or a provocative question are \
all wanted. Write the most arresting thing about the story you can, and go hard.
- A question-led title can prime an assumption the video complicates or answers \
"no" to — "Did NASA Deliberately Sacrifice the Challenger Crew?" is exactly the \
shape to use.
- VARY THE SHAPE. The user message lists what has already been published. Do not \
return the grammatical shape those titles use. A channel whose every title reads \
"The <thing> That <verb>ed <someone>" has taught its audience that the videos are \
interchangeable, and they stop reading past the third word. Question-led, \
number-led, a plain declarative or a two-part title are all available.

PUNCH PHRASE:
- punch_phrase: 2-6 words copied EXACTLY, word for word, from the last sentence \
of scene 5 — the words that land the ending (the number, the irony, the detail \
that makes it worse). It is slammed on screen as the narrator says it, so it \
must be words the narration actually speaks, in that order.
- GOOD: "illegal to wear the medals", "twenty minutes"
- BAD: a summary of the ending that is not in the narration.

COMMENT QUESTION:
- One question, posted as the first comment under the video. It must be answerable by someone who just watched THIS video and has an opinion — who was actually at fault, which moment was the point of no return, what they would have done. Name the people or the choice; never ask "what do you think?" on its own.
- GOOD: "Nolan carried the order and Lucan read it. Which of them do you blame?"
- BAD:  "Let me know your thoughts in the comments!"

THUMBNAIL:
- thumbnail_text: THREE OR FOUR WORDS ONLY, readable at 210 pixels wide. Not a \
sentence, no punctuation. It must not simply repeat the title — the two are read \
together, so they should say different things.
- Extract the strongest emotionally loaded interpretation the story supports, in \
one of these shapes: [Actor] + [loaded noun] ("NASA'S FATAL DECISION"), [Actor] + \
[dramatic verb] ("ENGINEERS IGNORED"), or [dramatic event] + [consequence] ("SUNK \
IN 20 MINUTES"). Pick whichever shape carries this story's actual point of blame \
or consequence — do not force an actor in if the story has none. [Actor] follows \
the same recognisability test as the title: an unrecognised person's name never \
goes here — use their role instead ("A NAZI WAR CRIMINAL", not "GÖRING'S ESCAPE").
- GOOD: "NASA'S FATAL DECISION", "ENGINEERS IGNORED", "SUNK IN 20 MINUTES", "THE \
WRONG VALLEY", "ONE MISSING BOLT".
- BAD:  "The story of how a ship sank", "AMAZING HISTORY FACT" — too flat, or too \
generic to be about this story.
- thumbnail_badge: the year, or a single number, e.g. "1628".

OUTPUT: Only a valid JSON object, no markdown fences, with this exact shape:
{
  "title": "consequence-first title within the stated character limit, one searchable proper noun",
  "description": "2 sentences summarizing the story + 3 relevant hashtags",
  "thumbnail_text": "three or four words, no punctuation",
  "thumbnail_badge": "the year",
  "comment_question": "one specific question naming someone or something from the story",
  "punch_phrase": "2-6 words copied exactly from the last sentence of scene 5",
  "next_tease": "only when the user message names a NEXT story: at most 12 words teasing it",
  "tags": ["8-12 specific search tags, including the proper nouns from the story"],
  "scenes": [
    {"narration": "Sentence one. Sentence two.", "shots": [
      {"image_prompt": "... picture for sentence one ...", "archive_query": ""},
      {"image_prompt": "... picture for sentence two ...", "archive_query": "..."}
    ]}
  ]
}"""


def reply_text(response) -> str:
    """The model's text, wherever it put it: reasoning models on some
    providers leave "content" empty and put the answer in "reasoning" or
    "reasoning_content" (the same helper as longform_llm._reply_text)."""
    try:
        message = response.choices[0].message
    except (AttributeError, IndexError, TypeError):
        return ""
    for field in ("content", "reasoning", "reasoning_content"):
        text = getattr(message, field, None)
        if isinstance(text, str) and text.strip():
            return text
    return ""


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

# How many independent writers must call a topic already-published before the
# run stands down on their say-so. One is not enough: the weakest models in the
# chain answer {"duplicate": true} as an easy way out of a hard JSON task.
DUPLICATE_VOTES_NEEDED = int(os.getenv("DUPLICATE_VOTES_NEEDED", "2"))

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


# The prompt asks for 15 words per sentence; this is the backstop, with slack.
# A sentence a viewer has to rewind is a sentence that loses them, and there is
# no rewinding in a Shorts feed — an overlong sentence is a comprehension bug,
# not a style choice.
MAX_SENTENCE_WORDS = 25


def _overlong_sentence(scenes):
    """The first narration sentence over MAX_SENTENCE_WORDS, or None."""
    for s in scenes:
        for sentence in re.split(r"(?<=[.!?])\s+", s.get("narration") or ""):
            if len(sentence.split()) > MAX_SENTENCE_WORDS:
                return sentence
    return None


def _validate(data: dict, title_max: int = 80, avoid_titles=(),
              series_name: str = "") -> bool:
    # Every rejection says why. These first two checks used to return False in
    # silence, and they are the ones a SALVAGED reply fails: _salvage_truncated
    # recovers whatever finished before the model ran out of tokens, which is
    # usually three scenes or a last scene with no image_prompt. The run of
    # 2026-08-28 logged "Script failed validation, retrying..." twice with no
    # reason on either, so there was no way to tell a truncated reply apart
    # from a script the word window had just rejected — which matters, because
    # that window was narrowed from 190 to 160 in the same week.
    scenes = data.get("scenes")
    if not isinstance(scenes, list):
        print(f"⚠️ Script rejected: no scenes list "
              f"(got {type(scenes).__name__}).")
        return False
    if not (4 <= len(scenes) <= 6):
        print(f"⚠️ Script rejected: {len(scenes)} scene(s), needs 4-6 — "
              "usually a reply that was cut off mid-object.")
        return False
    for i, s in enumerate(scenes):
        if not s.get("image_prompt"):
            first = next((x for x in s.get("shots") or []
                          if isinstance(x, dict) and x.get("image_prompt")), None)
            if first:
                s["image_prompt"] = first["image_prompt"]
        missing = [f for f in ("narration", "image_prompt") if not s.get(f)]
        if missing:
            print(f"⚠️ Script rejected: scene {i} is missing "
                  f"{' and '.join(missing)}.")
            return False
    total_words = sum(len(s["narration"].split()) for s in scenes)
    if not (90 <= total_words <= 160):  # ~40-60s once the outro tease is added
        print(f"⚠️ Script rejected: {total_words} words, needs 90-160.")
        return False
    overlong = _overlong_sentence(scenes)
    if overlong:
        print(f"⚠️ Script rejected: a sentence runs {len(overlong.split())} "
              f"words and cannot be followed at speaking speed. ({overlong!r})")
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


# Phrases that promise significance without specifying anything. The test used
# to be whether the title still makes sense with the subject swapped out: "The
# Hidden WW2 Battle" works equally well for any battle, which was supposed to be
# what made it useless.
#
# It wasn't. "Soviet Invasion of Kuril Islands 1945: The Hidden WW2 Battle" —
# published before this list existed to catch it — measured the BEST
# click-through rate of any long-form video on the channel (3.67%, against a
# ~1.8% average for the rest of the long-form catalog). One data point isn't
# proof, but it directly contradicts the theory this list was built on, and the
# channel's actual numbers (27 subscribers, 10k views, 67 videos, 1.67% overall
# CTR) say the restrained approach was not working either. So this list is now
# retired by default — see CLICKBAIT_MODE above — and kept only as the fallback
# for CLICKBAIT_MODE=off.
BANNED_TITLE_PHRASES = (
    "the hidden", "the untold", "untold story", "what really happened",
    "the forgotten", "the secret", "you won't believe", "you wont believe",
    # Not "the truth about": dropping "shocking" from this list let "The
    # SHOCKING Truth About HMS Hood" through, because the adjective broke the
    # phrase. The empty promise is "truth about", whatever is stacked in front.
    "truth about", "will blow your mind", "nobody talks about",
    "no one talks about",
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
        print("⚠️ Title rejected: the writer returned no title.")
        return False

    low = text.lower()
    if not CLICKBAIT_MODE:
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


# The spoken frame is "Next file: <tease>." — the tease itself has to stay short
# enough that the whole outro is one breath.
MAX_TEASE_WORDS = 14
MIN_TEASE_WORDS = 5

# Words that begin a trailing subordinate clause. When a pool line has to be cut
# to length, cutting HERE ends the tease on a complete thought — a plain
# truncation at the word limit produced "the airliner that ran out of fuel at
# 41,000 feet after a conversion between", which stops mid-phrase and is worse
# than saying nothing. Only clause openers: "of", "at" and "that" sit inside
# noun phrases, so cutting at those would strip the mechanism instead.
_CLAUSE_BREAKS = {"after", "before", "because", "when", "while", "since",
                  "although", "though", "despite", "following", "until",
                  "unless", "whereas", "having"}

# Weaker breaks, used only when a line must be cut and no real clause opener is
# available. "The Sanhedrin rule in Jewish law that a unanimous guilty verdict
# in a capital case results in acquittal" has no _CLAUSE_BREAKS at all, so a
# plain cut at the word limit ended on "in a capital". Cutting at "that" gives
# "the Sanhedrin rule in Jewish law", which is short but is a whole thought.
_SOFT_BREAKS = {"that", "which", "who", "whose", "where"}
SOFT_BREAK_MIN_WORDS = 8

# Function words that cannot be the last word spoken.
_DANGLING = _CLAUSE_BREAKS | {
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "at", "by",
    "for", "from", "with", "into", "onto", "over", "under", "that", "which",
    "who", "whose", "its", "their", "his", "her", "it", "as", "than", "then",
    "he", "she", "they", "we", "you", "what", "whom", "there", "this", "these",
    # Auxiliaries and negators: a clause cut after one is always mid-thought
    # ("...a territory Britain did not yet", "...told Western leaders what he had").
    "had", "has", "have", "was", "were", "is", "are", "be", "been", "being",
    "did", "do", "does", "will", "would", "can", "could", "should", "must",
    "may", "might", "not", "yet", "so", "such", "own", "more", "most",
}


def _tease_from_topic(topic: str) -> str:
    """A speakable tease built from a pool entry, for when the writer's own is
    missing or unusable.

    Pool entries are one descriptive sentence — subject, year, then the clause
    that makes the story worth telling. The last clause is the tease: for "The
    Gimli Glider (1983), Air Canada Flight 143, the airliner that ran out of
    fuel at 41,000 feet after a conversion between pounds and kilograms" it is
    the airliner clause, which is exactly what should be spoken after "Next
    file:". Split points require a letter after the comma so "41,000" survives.
    """
    text = re.sub(r"\s*\([^)]*\)", "", topic or "").strip()
    parts = [p.strip() for p in re.split(r",\s+(?=[A-Za-z])", text) if p.strip()]
    if not parts:
        return ""

    # The LONGEST clause first: pool lines put the mechanism in whichever
    # clause they spend the most words on. Taking the trailing clause instead
    # picked up codas — "and was not believed" on the Jan Karski line, "because
    # unanimity suggests the court stopped thinking" on the Sanhedrin one —
    # which are the reaction to the hook, not the hook.
    for candidate in (max(parts, key=len), parts[-1], parts[0]):
        clause = _speakable_clause(candidate)
        if clause:
            return clause
    return ""


def _speakable_clause(clause: str) -> str:
    """One pool clause, cut to something a narrator can finish. "" if nothing
    of speakable length survives."""
    for lead in ("where ", "when ", "which ", "and ", "in part by ", "by ",
                 "the story of ", "because ", "although ", "though ",
                 "against ", "who "):
        if clause.lower().startswith(lead):
            clause = clause[len(lead):]
            break

    words = clause.split()
    if len(words) > MAX_TEASE_WORDS:
        words = words[:MAX_TEASE_WORDS]
        # Cut at the last clause opener rather than mid-phrase — but only when
        # the line actually had to be cut. A tease that already fits is a
        # complete thought, and breaking it at "after" would throw away the
        # mechanism it was chosen for.
        breaks = [i for i, w in enumerate(words)
                  if w.strip(",.").lower() in _CLAUSE_BREAKS
                  and i >= MIN_TEASE_WORDS]
        if not breaks:
            # Last resort, and it must still leave a substantial thought. Cut
            # at the first "who"/"that" available and the Challenger line
            # collapses to "the Challenger disaster and the engineers", losing
            # the half that matters; letting it run to the word limit instead
            # keeps "...who argued against launching in the cold".
            breaks = [i for i, w in enumerate(words)
                      if w.strip(",.").lower() in _SOFT_BREAKS
                      and i >= SOFT_BREAK_MIN_WORDS]
        if breaks:
            words = words[:breaks[-1]]
    # No infinitive-trimming pass here on purpose: "to" cannot be told from a
    # preposition without parsing, and a rule that dropped a trailing "to VERB"
    # also ate the best line in the pool — "drawn in five weeks by a lawyer who
    # had never been to India" became "...who had never".
    while words and words[-1].strip(",.").lower() in _DANGLING:
        words.pop()

    clause = " ".join(words).rstrip(".,;: ")
    if len(clause.split()) < MIN_TEASE_WORDS:   # nothing speakable survived
        return ""
    # Sentence case is the writer's job; mid-sentence this reads as a name.
    if clause.split()[0] in ("The", "A", "An"):
        clause = clause[0].lower() + clause[1:]
    return clause


def normalize_next_tease(data: dict, next_topic: str) -> dict:
    """Fills next_tease, in place. Every path has to survive a writer that
    ignored the instruction — the tease is worth having, never worth a retry."""
    if not next_topic:
        data["next_tease"] = ""
        return data
    supplied = (data.get("next_tease") or "").strip().strip('"“”')
    supplied = supplied.rstrip(".!?…").strip()
    low = supplied.lower()
    if low.startswith("next file"):        # the writer repeated the frame
        supplied = supplied[len("next file"):].lstrip(" :,-—–")
    # The curated topic line first. The writer's own tease is spoken without
    # any fact check, and twice in a row it said something false: "a
    # declaration that started a war lasting a century" (run #123) and "the
    # memorandum that let Ukraine keep nukes" (run #124; Ukraine gave them
    # up). A clause cut from the topic line says only what the line says.
    from_topic = _tease_from_topic(next_topic)
    if from_topic:
        supplied = from_topic
    elif not supplied or len(supplied.split()) > MAX_TEASE_WORDS:
        supplied = ""
    data["next_tease"] = supplied
    return data


MIN_PUNCH_WORDS, MAX_PUNCH_WORDS = 2, 6

# The turn (docs/SHORTS_SPEC.md 5): one short line spoken to the viewer, in a
# middle scene. A sentence counts when it is short and speaks to the viewer:
# "you", or one of the storyteller's imperatives.
TURN_MAX_WORDS = 10
MAX_TURN_LINES = 2
_ADDRESS = re.compile(r"\b(you|your|watch|remember|here'?s|here is|look at|"
                      r"listen)\b", re.IGNORECASE)
_QUOTED = re.compile(r"[\"“][^\"”]*[\"”]")


def _turn_lines(narration: str):
    text = _QUOTED.sub("", narration or "")
    return [x for x in re.split(r"(?<=[.!?])\s+", text.strip())
            if x and len(x.split()) <= TURN_MAX_WORDS and _ADDRESS.search(x)]


def spec_issues(data: dict, has_facts: bool) -> list:
    """The writing rules of docs/SHORTS_SPEC.md that the prompt asks for and
    _validate does not already check. Each problem is one short sentence,
    handed back to the writer on the next attempt."""
    scenes = [s.get("narration") or "" for s in data.get("scenes") or []]
    problems = []
    if len(scenes) >= 3:
        middle = [t for s in scenes[1:-1] for t in _turn_lines(s)]
        everywhere = middle + _turn_lines(scenes[-1])
        if _turn_lines(scenes[0]):
            problems.append("the hook speaks to the viewer; the line to the "
                            "viewer belongs in scene 3 or 4")
        elif not middle:
            problems.append("there is no line spoken to the viewer at the "
                            "turn (one line of at most 8 words in scene 3 or "
                            "4, like \"Now watch what the captain does next.\")")
        elif len(everywhere) > MAX_TURN_LINES:
            problems.append("the narrator speaks to the viewer "
                            f"{len(everywhere)} times; once is the rule")
    # One picture per sentence (docs/SHORTS_SPEC.md 12). A writer that gave
    # every multi-sentence scene a single picture ignored the shot list.
    multi = [s for s in data.get("scenes") or []
             if len(re.split(r"(?<=[.!?])\s+", (s.get("narration") or "").strip())) > 1]
    if multi and all(len([x for x in s.get("shots") or [] if isinstance(x, dict)
                          and x.get("image_prompt")]) <= 1 for s in multi):
        problems.append("every scene has a single picture; give each scene a "
                        "\"shots\" list with one image_prompt per sentence")
    if not has_facts:
        # With no source to check a quote against, there are no quotes.
        for s in scenes:
            for q in re.findall(r"[\"“]([^\"”]{12,})[\"”]", s):
                problems.append(f"the quote \"{q[:50]}\" has no source to "
                                "be checked against; do not quote anyone")
    return problems


def _tokens(text: str):
    return [re.sub(r"[^a-z0-9]", "", w.lower()) for w in (text or "").split()]


def normalize_punch_phrase(data: dict) -> dict:
    """Keeps punch_phrase only if the last scene really speaks it, in place.

    The phrase is timed from the scene's word timings, so a phrase the
    narration never says cannot be placed at all. A writer that ignored the
    field, or paraphrased, falls back to the last sentence when that sentence
    is short enough to be the phrase itself, and otherwise to no card.
    """
    scenes = data.get("scenes") or []
    last = (scenes[-1].get("narration") or "") if scenes else ""
    spoken = _tokens(last)
    supplied = (data.get("punch_phrase") or "").strip().strip('"“”')
    wanted = [t for t in _tokens(supplied) if t]
    ok = MIN_PUNCH_WORDS <= len(wanted) <= MAX_PUNCH_WORDS and any(
        spoken[i:i + len(wanted)] == wanted
        for i in range(len(spoken) - len(wanted) + 1))
    if not ok:
        sentences = [x for x in re.split(r"(?<=[.!?])\s+", last.strip()) if x]
        tail = sentences[-1].rstrip(".!?") if sentences else ""
        supplied = tail if MIN_PUNCH_WORDS <= len(tail.split()) <= MAX_PUNCH_WORDS \
            else ""
    data["punch_phrase"] = supplied.strip(" .,;:!?")
    return data


def generate_video_script(topic: str = None, avoid_titles=(),
                         title_max: int = 80, max_retries_per_model: int = 2,
                         series_name: str = "", next_topic: str = "",
                         facts=None):
    """Generates the daily script.

    `topic` is chosen by the caller now, because choosing it well needs the
    channel's published history and this module has no business calling the
    YouTube API. Passing None keeps the old behaviour — the bare date rotation,
    which is what the fallback path wants.

    `avoid_titles` are the channel's most recent titles. They are shown to the
    writer so it cannot answer a topic it has already covered with a reworded
    version of the same title, which is the exact shape the duplicates took.

    `facts` is shorts_facts.research_short()'s result: the story's Wikipedia
    article and its verified fact sheet. With it, the writer works from the
    sheet and the finished script is checked against the article; without
    it, the writer works from memory as it always did.
    """
    print("🧠 Initializing LLM Engine...")

    # Same writer as the long-form lanes: Gemini's free tier when a key is
    # present, OpenRouter's free models otherwise.
    from llm_provider import (get_pool, describe_provider,
                              permanent_failure)
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

    # Which of the channel's three episode formats this story is. The frame used
    # to be "one decision and what it cost" for everything, which is why the
    # Dancing Plague — no decision, no villain — came out sounding like a
    # military blunder. See src/formats.py.
    episode_format = topics_format_of(topic)
    print(f"🏷️  Format: {formats.name_of(episode_format)} — "
          f"{formats.get(episode_format)['promise']}")

    # The outro tease: the next episode's story, compressed to one clause the
    # narrator speaks after "Next file:". Named next to the story it belongs
    # to, because "a story for another day" teases nothing and converts nobody.
    tease_block = ""
    if next_topic:
        tease_block = (
            "\nNEXT EPISODE: the video after this one covers this story:\n"
            f"{next_topic}\n"
            'Return a "next_tease" field: ONE clause, at most 12 words, teasing '
            "that next story. It is spoken after the words \"Next file:\", so do "
            "not repeat them and do not write a full sentence with its own "
            "subject-verb framing of this episode. State the next story's single "
            "most arresting concrete fact in plain words — someone who has never "
            "heard of the event must understand it and want it. A bare name is "
            "not a tease; what happened is.\n"
            'GOOD: "the airliner that ran out of fuel at 41,000 feet"\n'
            'BAD: "an incredible story you won\'t believe"\n')

    if facts:
        from shorts_facts import prompt_block
        facts_block = prompt_block(facts)
    else:
        # No source to check a quote against, so no quotes: a remembered
        # quote is the likeliest thing a model invents.
        facts_block = ("Stick to established facts about this event. Do not "
                       "quote anyone.\n")

    user_prompt = (
        f"Write today's YouTube Short script about this specific true story:\n\n{topic}\n\n"
        f"FORMAT — {formats.name_of(episode_format)}: "
        f"{formats.brief_of(episode_format)}\n\n"
        f"{facts_block}"
        f"{avoid_block}"
        f"{tease_block}"
        "CRITICAL: the total narration across all 5 scenes combined must be "
        "110-130 words. Scripts under 100 words will be rejected.\n"
        "CRITICAL: scene 1 must be at most 12 words, must not begin with setup or a "
        "date, and must contain a number, a name, or a superlative. A scene 1 that "
        "reads as an introduction will be rejected.\n"
        f"CRITICAL: the title must be at most {title_max} characters, including "
        "spaces. A longer title will be rejected.\n"
        "Output ONLY the JSON object."
    )

    # Vendors whose model(s) answered "this is already published". Tracked by
    # vendor, not by model, and collected rather than obeyed on sight — see
    # the duplicate branch below.
    duplicate_vote_vendors = set()
    # What the last script got wrong against the source article, handed to the
    # next attempt so it corrects the figure instead of rolling the dice again.
    fact_feedback = ""

    for model in pool.models:
        client = pool.client_for(model)
        for attempt in range(1, max_retries_per_model + 1):
            print(f"🤖 Trying {model} (attempt {attempt}/{max_retries_per_model})...")
            try:
                kwargs = dict(
                    model=model,
                    messages=[
                        {"role": "system", "content": get_system_prompt()},
                        {"role": "user", "content": user_prompt + fact_feedback},
                    ],
                    temperature=0.8,
                    max_tokens=4000,
                )
                if "gpt-oss" in model:
                    # A reasoning model spends the same 4,000 tokens on
                    # thinking first; on Groq it thought until nothing was
                    # left and answered with empty content (run #123).
                    kwargs["reasoning_effort"] = "low"
                try:
                    response = client.chat.completions.create(**kwargs)
                except TypeError:
                    kwargs.pop("reasoning_effort", None)
                    response = client.chat.completions.create(**kwargs)
                content = reply_text(response)
                if not content or not content.strip():
                    raise ValueError("model returned empty content")
                data = _extract_json(content)
                if data.get("duplicate") and not data.get("scenes"):
                    # A duplicate report is a VOTE, not a verdict.
                    #
                    # This used to return immediately — "trust it" — and on
                    # 2026-08-29 a 2.6B free model that had just failed to
                    # produce valid JSON on eight other attempts answered
                    # {"duplicate": true} to the Enabling Act, a topic the
                    # channel has never covered, and killed the whole run
                    # before the remaining models were even tried.
                    #
                    # The real duplicate guard is upstream and reads the
                    # channel itself: covered_topics() had already excluded 43
                    # published topics from that very run. This is only a
                    # backstop for what token matching misses, so it needs
                    # corroboration before it outranks a live API read.
                    #
                    # "Corroboration" means independent sources, not just a
                    # second model ID. On 2026-09-16 inclusionai/ling-3.0-
                    # flash-vl and inclusionai/ling-3.0-flash-sante — the same
                    # base model wearing two fine-tune names — both voted
                    # "duplicate" on a topic the channel's own history had
                    # just confirmed was NOT published (it was that video's
                    # own promised next episode), and two votes from one
                    # vendor was enough to kill a legitimate run. Tallying by
                    # vendor instead of by model means the same shared
                    # hallucination can no longer out-vote a real API read.
                    vendor = model.split("/", 1)[0] if "/" in model else model
                    duplicate_vote_vendors.add(vendor)
                    print(f"🚫 {model} reports this story is already "
                          f"published ({len(duplicate_vote_vendors)} of "
                          f"{DUPLICATE_VOTES_NEEDED} independent vendor(s)).")
                    if len(duplicate_vote_vendors) >= DUPLICATE_VOTES_NEEDED:
                        print("🚫 Enough independent writers agree — "
                              "treating the topic as already published.")
                        return None
                    break   # this model has nothing else to offer; next one
                if not _validate(data, title_max, avoid_titles, series_name):
                    print("⚠️ Script failed validation, retrying...")
                    continue
                problems = spec_issues(data, bool(facts))
                for problem in problems:
                    print(f"⚠️ Spec check: {problem}.")
                if facts:
                    from shorts_facts import unsupported_claims
                    found = unsupported_claims(data, facts["text"])
                    for problem in found[:6]:
                        print(f"⚠️ Fact check: {problem}.")
                    problems += found[:6]
                if problems:
                    fact_feedback = (
                        "\n\nYOUR LAST SCRIPT WAS REJECTED: "
                        + "; ".join(problems) + ". Fix exactly these; use "
                        "only the fact sheet's names, figures and quotes.")
                    continue
                if facts:
                    data["source_title"] = facts["title"]
                    data["source_url"] = facts["url"]
                    print("✅ Fact check: every figure and name is in "
                          f"{facts['url']}")
                data["topic"] = topic
                data["format"] = episode_format
                normalize_thumbnail_fields(data)
                normalize_next_tease(data, next_topic)
                normalize_punch_phrase(data)
                from shorts_shots import normalize_shots
                normalize_shots(data)
                print(f"✅ Script generated by {response.model}")
                return data
            except Exception as e:
                reason = permanent_failure(e)
                if reason:
                    # Asking again in two seconds gets the same refusal. Move
                    # to the next model instead of spending a second call and
                    # a sleep on a settled answer.
                    print(f"⏭️  {model} unavailable: {reason} — skipping.")
                    break
                print(f"⚠️ {model} failed: {e}")
                time.sleep(15 if "429" in str(e) else 2)

    print("❌ All models failed.")
    return None


if __name__ == "__main__":
    script = generate_video_script()
    if script:
        print(json.dumps(script, indent=2))
