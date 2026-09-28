"""
Research for the daily Shorts: the long-form fact sheet, at Shorts size.

WHY
The Shorts writer worked from memory. Its prompt said "Stick to established
facts about this event", and nothing checked that it had: with the hard-sell
titles on and no person reading a script before upload, one invented figure
under a confident title is the worst thing this lane can publish.

Story mode (story_mode.py) already solved this for the long-form lanes: the
event's own Wikipedia article, and a fact sheet whose quotes and figures are
checked against that text. The Shorts now use the same two steps, and then
check the finished script against the article as well:

  - every figure in the narration, title or thumbnail (digits, or number
    words like "six hundred") must appear in the article; a round figure may
    round a real one ("over 57,000" for 57,470), nothing else may;
  - every capitalised word that does not start a sentence (a name, a ship,
    a place) must appear in the article.

A script that fails is sent back to the writer with the offending figure or
name, like any other validation failure.

SHORTS_FACTS
  prefer   (default) research first; when no article or sheet can be had,
           write from memory as before, and say so loudly in the log.
  require  a run with no fact sheet stops instead of publishing.
  off      the old behaviour.
"""

import os
import re

import story_mode as sm

SHORTS_FACTS = os.getenv("SHORTS_FACTS", "prefer").strip().lower()

# A Short needs the story, not the whole article, and the fact sheet call
# costs the article's length in input tokens on free providers with small
# allowances. The check against the finished script uses the full text.
SHORT_ARTICLE_CHARS = 60000

# Figures below this are "one order", "two hours", "three ships": they are the
# grammar of the story, not claims about it, and number words this small
# appear in every article anyway.
MIN_CHECKED_FIGURE = 13

# How far a spoken round figure may sit from the article's own number.
ROUNDING_TOLERANCE = 0.10


def research_short(topic):
    """{"sheet", "title", "url", "text"} for this story, or None."""
    if SHORTS_FACTS == "off":
        return None
    try:
        from llm_provider import get_pool
        from longform_llm import _call
        pool = get_pool()
        # Pool lines start with the event's name ("The Battle of Isandlwana
        # (1879), where..."), which is usually the article's own title. Try
        # it before spending a model call on naming the article.
        subject = (topic or "").split("(")[0].split(",")[0].strip()
        queries = [subject] if 0 < len(subject.split()) <= 8 else []
        article = None
        for query in queries + [q for q in sm.article_queries(pool, topic, _call)
                                if q not in queries]:
            article = sm.research(query)
            if article:
                break
        if not article:
            print(f"⚠️ Research: no Wikipedia article found for {topic[:70]!r}.")
            return None
        print(f"📚 Research: {article['title']} ({len(article['text']):,} chars)")
        short = dict(article, text=article["text"][:SHORT_ARTICLE_CHARS])

        def call(p, system, user, max_tokens):
            return _call(p, system, user, max_tokens=max_tokens)

        sheet = sm.fact_sheet(pool, topic, short, call)
    except Exception as e:
        print(f"⚠️ Research failed ({type(e).__name__}: {e}).")
        return None
    if not sheet:
        print("⚠️ Research: the fact sheet came back too thin to use.")
        return None
    return {"sheet": sheet, "title": article["title"], "url": article["url"],
            "text": article["text"]}


def prompt_block(facts):
    """The fact sheet, and the rules for using it, for the writer's prompt."""
    return (
        f"FACT SHEET (from the Wikipedia article \"{facts['title']}\"):\n"
        f"{sm.sheet_text(facts['sheet'])}\n\n"
        "FACTS RULE: every name, date, figure and quote in the narration, "
        "title and thumbnail must come from this fact sheet. Write figures as "
        "digits exactly as the sheet gives them; you may round a large one "
        "down or up in speech (\"over 57,000\" for 57,470), never invent one. "
        "The detail most viewers do not know is in this sheet: find it there, "
        "never from memory.\n"
        "QUOTE: if the sheet has a quote from a person in this story that is "
        "short enough to say in one breath (about 20 words at most), use one: "
        "copy it word for word inside double quotes and say who said it "
        "(\"The captain wrote: \"...\"\"). Words in quotes that are not in "
        "the sheet will be rejected. No quote is better than an altered one.\n")


# ---------------------------------------------------------------------------
# Checking the finished script
# ---------------------------------------------------------------------------

_UNITS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve "
    "thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {w: 10 * (i + 2) for i, w in enumerate(
    "twenty thirty forty fifty sixty seventy eighty ninety".split())}
_SCALES = {"hundred": 100, "thousand": 1000, "million": 1_000_000,
           "billion": 1_000_000_000}


def word_numbers(text):
    """Integers spelled out in words: "six hundred" -> 600, "twenty-three
    thousand" -> 23000. Plurals ("hundreds") are approximate and not read.
    Two small words in a row ("sixteen twenty") end a number rather than add
    up, so a year said aloud is not misread as 36."""
    tokens = re.findall(r"[a-z]+", (text or "").lower().replace("-", " "))
    found, total, current, last, active = [], 0, 0, None, False

    def close():
        nonlocal total, current, last, active
        if active:
            found.append(total + current)
        total, current, last, active = 0, 0, None, False

    for tok in tokens:
        if tok in _UNITS or tok in _TENS:
            small = _UNITS.get(tok, _TENS.get(tok))
            if last == "tens" and tok in _UNITS and 0 < small < 10:
                current += small
                last = "unit"
                continue
            if last in ("unit", "tens"):
                close()
            current += small
            last = "tens" if tok in _TENS else "unit"
            active = True
        elif tok in _SCALES and active:
            scale = _SCALES[tok]
            if scale == 100:
                current = max(current, 1) * 100
            else:
                total += max(current, 1) * scale
                current = 0
            last = "scale"
        elif tok == "and" and active and last == "scale":
            continue
        else:
            close()
    close()
    return found


def _digit_numbers(text):
    """Numbers written in digits, as ints (commas dropped) or, for decimals,
    as the normalised string."""
    out = []
    for raw in re.findall(r"\d[\d,]*(?:\.\d+)?", text or ""):
        raw = raw.rstrip(",")
        clean = raw.replace(",", "")
        out.append(float(clean) if "." in clean else int(clean))
    return out


def _article_numbers(article_text):
    return set(_digit_numbers(article_text)) | set(word_numbers(article_text))


def _is_round(n):
    """At most two significant figures: 57,000, 600, 1,500 — the kind of
    number a narrator rounds to."""
    if not isinstance(n, int) or n < 100:
        return False
    digits = str(n).rstrip("0")
    return len(digits) <= 2


def figure_supported(n, article_nums):
    if n in article_nums:
        return True
    if isinstance(n, float) and n.is_integer() and int(n) in article_nums:
        return True
    if _is_round(n):
        return any(isinstance(m, (int, float)) and m > 0
                   and abs(m - n) / m <= ROUNDING_TOLERANCE
                   for m in article_nums)
    return False


def _sentence_initial(text):
    """Positions of words that start a sentence (or a quote), whose capital
    says nothing about whether they are a name."""
    starts = set()
    for m in re.finditer(r"(?:^|[.!?:;—]\s+|[\"“‘]\s*)(\S+)", text or ""):
        starts.add(m.start(1))
    return starts


def unsupported_claims(script, article_text):
    """Figures and names in the script that the article does not contain.
    Returns a list of short human-readable problems, empty when clean."""
    art = sm._norm(article_text)
    art_nums = _article_numbers(article_text)
    problems = []

    spoken = [s.get("narration") or "" for s in script.get("scenes") or []
              if not s.get("is_outro")]
    marketing = [script.get("title") or "", script.get("thumbnail_text") or "",
                 str(script.get("thumbnail_badge") or "")]

    for text in spoken + marketing:
        figures = _digit_numbers(text) + word_numbers(text)
        for n in figures:
            if n < MIN_CHECKED_FIGURE:
                continue
            if not figure_supported(n, art_nums):
                shown = f"{n:,}" if isinstance(n, int) else str(n)
                problems.append(f"the figure {shown} (in {text[:60]!r}) is not "
                                "in the source article")

    # Names only from the narration: the title deliberately swaps names for
    # roles ("A Soviet Engineer"), which are not proper nouns of the article.
    for text in spoken:
        initial = _sentence_initial(text)
        for m in re.finditer(r"[A-Z][a-zA-Z'’-]{2,}", text):
            if m.start() in initial:
                continue
            word = re.sub(r"['’]s$", "", m.group(0)).lower()
            if word not in art:
                problems.append(f"the name {m.group(0)!r} is not in the source "
                                "article")

    # Anything the script puts in quotation marks is a quote, and a quote is
    # only real if the article carries it word for word.
    for text in spoken:
        for q in re.findall(r"[\"“]([^\"”]{12,})[\"”]", text):
            if sm._norm(q).strip(" .,!?") not in art:
                problems.append(f"the quote \"{q[:70]}\" is not word for word "
                                "in the source article")

    # One report per problem, in the order found.
    return list(dict.fromkeys(problems))
