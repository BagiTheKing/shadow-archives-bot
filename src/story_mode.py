"""
Story mode: research first, outline second, chapters written from both.

WHY THIS EXISTS
The six-chapter arc (Cost -> Claim -> Plan -> Decision -> Chain -> Verdict)
is a courtroom case built for one decision and one disaster. The 1967
Six-Day War video showed what it does to a big story: the same idea said
four times in two minutes, two and a half minutes of what one man
"believed" before anything happened, the war's actual six days squeezed
into twenty seconds, a fake cliffhanger at every chapter end, and, written
from memory with no source, an invented ship and the West Bank and Golan
credited to Egypt.

Story mode tells the story instead of arguing a case:
1. research(): the event's own Wikipedia article, as plain text.
2. fact_sheet(): a model extracts a dated timeline, the people, places,
   figures and verbatim quotes — ONLY from that text. Every quote must be
   a substring of the article and every figure's digits must appear in it;
   anything else is dropped before a chapter is written.
3. outline(): one call plans the video as a story: a cold open on the most
   gripping moment, then chronological chapters that follow named people,
   with the space given where the drama is (the climax gets the most) and
   at most two earned end-of-chapter hooks.
4. The existing chapter writer writes each chapter from its beats and the
   fact sheet, under STORY_SYSTEM_PROMPT (scenes over reasoning, varied
   sentence length, no facts outside the sheet).

Every downstream feature keeps working: chapters carry the same pacing
roles (open / build / pivot / drive / close), so grades, date stamps, the
clock before the decision, maps, the chain and the decision-maker card
land where they already do.
"""

import json
import os
import re

STORY_WORDS = int(os.getenv("STORY_WORDS", "2000"))     # ~12 min narrated
WIKI_API = "https://en.wikipedia.org/w/api.php"
# The whole article, not its first third: at 45,000 characters the Six-Day
# War outline lost the Syrian front and Nasser's resignation entirely.
ARTICLE_CHARS = 150000
_DROP_SECTIONS = ("See also", "References", "Notes", "Further reading",
                  "External links", "Bibliography", "Sources", "Citations",
                  "Footnotes")

STORY_SYSTEM_PROMPT = """You write documentary narration for The Archive Paradox, a \
YouTube history channel. You are telling a TRUE STORY to someone who has never heard \
of it, and your job is to make them unable to stop watching.

FACTS:
- Use ONLY the facts, names, dates, figures and quotes given in the FACT SHEET below. \
Never add a detail that is not there — no invented ships, scenes, times, numbers or \
words. If a detail would help but is not in the sheet, write around it.
- A quote may only be used word for word as it appears in the sheet, attributed to \
its speaker.

STORYTELLING:
- Scenes, not summaries. Put the viewer in a place at a moment: who is in the room, \
what they do, what happens next. Action verbs, not mental-state verbs — show what \
people DID, and let what they thought come through what they did and said.
- Follow the named people of this chapter. Give each one a reason we should care.
- Move forward in time. Anchor the viewer with dates and times from the sheet as you go: \
whenever the story reaches a new day, say its date the way a narrator speaks it \
("On the fifth of June", "On the twenty-second of May, 1967"), and say the clock time \
when the sheet gives one ("at seven forty-five that morning"). An on-screen time bar \
follows every date you say, so the viewer always knows when they are.
- Never restate what an earlier chapter already told. Every sentence adds something new.
- Rhythm: mix sentence lengths deliberately. Mostly medium sentences, a long one when \
the moment needs room, and short hard ones — three to six words — for impact. \
Never three long sentences in a row.
- Plain language for a general audience: explain any military, political or technical \
term in the same sentence.
- No jokes, no rhetorical questions to the viewer, no "we", no "you". No fake \
suspense lines. The events supply the drama.
- Do not take sides between present-day states. Narrate what people did and what it \
cost, attributed to them.

SIMPLICITY — the viewer has NO political, military or historical background:
- Name ONLY the people in this video's CAST. The first time each is named, say who \
they are in the same sentence ("Egypt's president, Gamal Abdel Nasser"); after \
that, remind the viewer now and then ("Nasser, Egypt's leader"). Everyone else is \
a role: "an Israeli general", "Jordan's king", "a UN officer".
- Name ONLY the PLACES listed for this video. Any other place is described in \
plain words ("a desert pass", "the border villages").
- No unit names or numbers (no brigades, divisions, battalions, squadrons by name \
or number): "Israeli tanks", "Egyptian troops".
- At most three figures in a chapter, rounded ("about 1,000", "nearly 20,000"), \
each used only for exactly what it measures.
- A quote is at most 40 words.
- One sentence, one idea. Nothing over 30 words.

IMAGE PROMPTS:
- One clear focal subject: one person, one object or one place, with the shot type \
first and the period's dress, vehicles and buildings named explicitly. One figure at \
most. Never hands as the subject.
- Every prompt must end with exactly: "{style}"
"""


# ---------------------------------------------------------------------------
# 1. Research
# ---------------------------------------------------------------------------

def research(topic):
    """{"title", "url", "text"} for the topic's Wikipedia article, or None."""
    from archive_engine import _polite_get
    r = _polite_get(WIKI_API, params={
        "action": "query", "list": "search", "srsearch": topic, "srlimit": 1,
        "format": "json"}, timeout=30)
    hits = (r.json().get("query") or {}).get("search") or []
    if not hits:
        return None
    title = hits[0]["title"]
    r = _polite_get(WIKI_API, params={
        "action": "query", "prop": "extracts", "explaintext": 1,
        "exsectionformat": "wiki", "titles": title, "redirects": 1,
        "format": "json"}, timeout=60)
    pages = list(((r.json().get("query") or {}).get("pages") or {}).values())
    text = (pages[0].get("extract") or "") if pages else ""
    # Cut the reference apparatus: it is long and carries no story.
    for sec in _DROP_SECTIONS:
        m = re.search(rf"\n==+\s*{re.escape(sec)}\s*==+", text)
        if m:
            text = text[:m.start()]
    if len(text) < 2000:
        return None
    return {"title": title, "text": text[:ARTICLE_CHARS],
            "url": "https://en.wikipedia.org/wiki/" + title.replace(" ", "_")}


# ---------------------------------------------------------------------------
# 2. Fact sheet
# ---------------------------------------------------------------------------

def _norm(s):
    s = (s or "").replace("’", "'").replace("‘", "'")
    s = s.replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", s).strip().lower()


def _names_ok(text, art):
    """Every capitalised word is in the article (a possessive "Gur's"
    matched as "Gur")."""
    return all(re.sub(r"'s$", "", w).lower() in art
               for w in re.findall(r"[A-Z][a-zA-Z'-]{2,}", text or ""))


def _digits_ok(text, art_digits):
    return all(d.rstrip(".,") in art_digits
               for d in re.findall(r"\d[\d,.]*", _norm(text)))


def verify_beats(plan, article_text):
    """The outline's beats and hooks get the fact sheet's check: a beat or
    hook naming a place, person or figure the article does not contain is
    dropped (v1 invented "the Lion's Gate" and a pre-dawn launch)."""
    art = _norm(article_text)
    digits = {d.rstrip(".,") for d in re.findall(r"\d[\d,.]*", art)}
    dropped = []
    for c in plan["chapters"]:
        kept = []
        for b in c["beats"]:
            if _names_ok(b, art) and _digits_ok(b, digits):
                kept.append(b)
            else:
                dropped.append(b)
        c["beats"] = kept
        if c.get("hook") and not (_names_ok(c["hook"], art)
                                  and _digits_ok(c["hook"], digits)):
            dropped.append("HOOK: " + c["hook"])
            c["hook"] = ""
    plan["chapters"] = [c for c in plan["chapters"] if c["beats"]]
    for key, name in (("cast", lambda c: c.get("name")), ("places", lambda p: p)):
        kept = [x for x in plan.get(key) or [] if _names_ok(name(x), art)]
        dropped += [f"{key.upper()}: {name(x)}" for x in plan.get(key) or []
                    if x not in kept]
        plan[key] = kept
    return dropped


def verify_sheet(sheet, article_text):
    """Drops every quote that is not verbatim in the article, every figure
    whose digits are not in it, and every person, place or timeline entry
    that names nothing the article names. Returns (sheet, dropped_count)."""
    art = _norm(article_text)
    art_digits = {d.rstrip(".,") for d in re.findall(r"\d[\d,.]*", art)}
    dropped = 0

    def names_ok(text):
        return _names_ok(text, art)

    def digits_ok(text):
        return all(d in art_digits or d.rstrip(".,") in art_digits
                   for d in re.findall(r"\d[\d,.]*", _norm(text)))

    out = {}
    for key, check in (
            ("quotes", lambda q: _norm(q.get("text")) in art and len(q.get("text", "")) > 8
             and names_ok(q.get("speaker"))),
            ("numbers", lambda n: digits_ok(str(n.get("value"))) and names_ok(n.get("what"))),
            ("people", lambda p: names_ok(p.get("name")) and p.get("name")),
            ("places", lambda p: names_ok(p if isinstance(p, str) else p.get("name"))),
            ("timeline", lambda t: digits_ok(t.get("date")) and names_ok(t.get("event"))
             and names_ok(" ".join(t.get("people") or [])))):
        kept = []
        for item in sheet.get(key) or []:
            try:
                ok = check(item)
            except Exception:
                ok = False
            if ok:
                kept.append(item)
            else:
                dropped += 1
        out[key] = kept
    return out, dropped


def fact_sheet(pool, topic, article, call):
    """The verified fact sheet for this story, or None."""
    prompt = (
        f"Story: {topic}\n\nSOURCE ARTICLE (Wikipedia, \"{article['title']}\"):\n"
        f"{article['text']}\n\n"
        "Extract a FACT SHEET for a documentary, using ONLY this article. "
        "Copy names, dates and figures exactly as the article writes them.\n"
        "- timeline: 25-45 dated events in order, from the first cause to the "
        "aftermath, covering EVERY phase and EVERY front or theatre the "
        "article describes, through to the end (ceasefire, resignation, "
        "treaty — whatever ends it). Each: date (as the article gives it, "
        "with the year), event (one plain sentence of what happened), "
        "place, people (names).\n"
        "- people: the 6-12 key people: name and their role at the time.\n"
        "- places: the key places.\n"
        "- numbers: 8-15 figures (troops, aircraft, casualties, distances, "
        "durations), each value as written and what it measures.\n"
        "- quotes: every direct quotation the article contains that a "
        "narrator could use, copied EXACTLY character for character, with "
        "its speaker. None if the article has none.\n"
        "Do not add anything the article does not state.\n\n"
        'Output ONLY JSON: {"timeline": [{"date": "", "event": "", "place": "", '
        '"people": [""]}], "people": [{"name": "", "role": ""}], "places": [""], '
        '"numbers": [{"value": "", "what": ""}], "quotes": [{"text": "", '
        '"speaker": ""}]}')
    data = call(pool, "You extract facts from a source text. Never add "
                "anything. Output only JSON.", prompt, max_tokens=8000)
    if not isinstance(data, dict) or not data.get("timeline"):
        return None
    sheet, dropped = verify_sheet(data, article["text"])
    print(f"📑 Fact sheet from {article['url']}: {len(sheet['timeline'])} "
          f"dated events, {len(sheet['people'])} people, "
          f"{len(sheet['numbers'])} figures, {len(sheet['quotes'])} verbatim "
          f"quotes ({dropped} unverifiable item(s) dropped).")
    if len(sheet["timeline"]) < 8:
        return None
    sheet["source"] = article["url"]
    return sheet


QUOTE_MAX_WORDS = 40


def sheet_text(sheet, chapter_span=None, cast=None):
    """The fact sheet as compact lines for a prompt. With a cast, only those
    people are listed, and only their quotes."""
    lines = ["TIMELINE:"]
    lines += [f"- {t['date']}: {t['event']}" + (f" ({t['place']})" if t.get("place") else "")
              for t in sheet["timeline"]]
    lines.append("PEOPLE:")
    people = [{"name": c["name"], "role": c.get("role", "")} for c in cast] \
        if cast else sheet["people"]
    lines += [f"- {p['name']}: {p.get('role', '')}" for p in people]
    if sheet["numbers"]:
        lines.append("FIGURES:")
        lines += [f"- {n['value']}: {n['what']}" for n in sheet["numbers"]]
    quotes = [q for q in sheet["quotes"]
              if len(q["text"].split()) <= QUOTE_MAX_WORDS
              and (not cast or _in_cast(q.get("speaker"), cast))]
    if quotes:
        lines.append("QUOTES (use only word for word):")
        lines += [f"- \"{q['text']}\" — {q['speaker']}" for q in quotes]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 3. Outline
# ---------------------------------------------------------------------------

ROLES = ("open", "build", "pivot", "drive", "close")
MAX_CAST = 5
MAX_PLACES = 5


def _surname(name):
    parts = [w for w in re.findall(r"[A-Za-z'-]+", name or "") if len(w) > 2]
    return parts[-1].lower() if parts else ""


def _in_cast(name, cast):
    sur = _surname(name)
    return bool(sur) and any(sur == _surname(c["name"]) for c in cast)


def fix_roles(chapters):
    """First chapter open, last close, exactly one pivot in between (the one
    the outline marked, else the middle), build before it, drive after."""
    n = len(chapters)
    marked = [i for i, c in enumerate(chapters) if c.get("role") == "pivot"
              and 0 < i < n - 1]
    pivot = marked[0] if marked else n // 2
    for i, c in enumerate(chapters):
        c["role"] = ("open" if i == 0 else "close" if i == n - 1 else
                     "pivot" if i == pivot else "build" if i < pivot else "drive")
    return chapters


def outline(pool, topic, sheet, call, total_words=STORY_WORDS):
    """{"question", "chapters": [{"title", "span", "pov", "role", "beats",
    "words", "hook"}]} or None."""
    prompt = (
        f"Story: {topic}\n\nFACT SHEET:\n{sheet_text(sheet)}\n\n"
        f"Plan a {total_words}-word documentary (about "
        f"{round(total_words / 165)} minutes) that tells this as a STORY a "
        "stranger cannot stop watching.\n"
        "RULES:\n"
        "- Chapter 1 is a cold open: 90-130 words dropping the viewer into the "
        "single most gripping MOMENT of the whole story (a time, a place, "
        "something happening), and it STOPS at the instant before the "
        "outcome: nothing is hit, destroyed, won or decided in it. It must "
        "not reveal any result a later chapter tells.\n"
        "- After it, go CHRONOLOGICAL: 5 more chapters (6 in all) from the "
        "first cause to the aftermath, told as ONE story, not parallel "
        "threads. Each has a dated span, follows 1-2 people from the cast "
        "(pov), and has 3-5 beats: concrete things that happen, taken from "
        "the timeline.\n"
        "- The viewer has NO background. Keep it SIMPLE:\n"
        f"  * cast: at most {MAX_CAST} people for the whole video, each with a "
        "plain role (\"Egypt's president\"). Pick the leaders on EVERY side "
        "so both sides are heard. Everyone else in the beats is a role "
        "(\"an Israeli general\"), never a name.\n"
        f"  * places: at most {MAX_PLACES} places for the whole video, only "
        "the ones the story cannot do without. Other places are described "
        "(\"a desert pass\").\n"
        "  * No unit names or numbers (brigades, divisions). Few figures, "
        "rounded, each only for what it measures.\n"
        "  * Basics first: the first chapter after the cold open begins with "
        "who is who (which countries or sides, who leads them) and why the "
        "key place matters, in plain words, before any events.\n"
        "  * Leave out side incidents that would need their own explanation "
        "and do not change the outcome.\n"
        "  * The leaders of the losing side come back in the last chapter: "
        "what they did and said afterwards.\n"
        "- The turning point (role \"pivot\") is its OWN SHORT chapter, "
        "150-250 words: the decisive choice itself — the room, the people, "
        "the vote or the order — and nothing else. The climax chapter comes "
        "right after it.\n"
        "- When several fronts, theatres or places run at the same time, "
        "give each its own chapter or its own unbroken run of beats. Never "
        "hop between fronts inside a chapter. Cover EVERY front and every "
        "major turn in the timeline — none may be left out.\n"
        "- Beats use ONLY the fact sheet: no place, name, time, number or "
        "action that is not in it.\n"
        "- Give space where the drama is: the climax and its events get the "
        "most words (at least a third of the total). Background gets the "
        "least. The last chapter (aftermath and what it changed) is about an "
        "eighth.\n"
        "- A story question, not a verdict: one short, sharp question a "
        "stranger wants answered (how, or at what cost, something turned out), "
        "under 15 words. It must be NEUTRAL — not asked from one side's point of view, and "
        "never framing one side as the threat or the victim. Every fear, "
        "claim or threat in a beat is attributed to whoever held it "
        "(\"Israel's leaders feared...\", \"Nasser declared...\"), never "
        "narrated as fact.\n"
        "- hook: at most TWO chapters end on a hook, and only where the next "
        "chapter pays it off. Every other hook is empty.\n"
        "- Titles are story beats (\"The Bluff\", \"7:45 a.m.\"), never "
        "abstract labels.\n"
        f"- Words across all chapters add up to {total_words}.\n\n"
        'Output ONLY JSON: {"question": "", "cast": [{"name": "", "role": ""}], '
        '"places": [""], "chapters": [{"title": "", '
        '"span": "", "pov": [""], "role": "open|build|pivot|drive|close", '
        '"beats": [""], "words": 0, "hook": ""}]}')
    data = call(pool, "You plan documentary stories. Output only JSON.",
                prompt, max_tokens=5000)
    chapters = (data or {}).get("chapters") if isinstance(data, dict) else None
    if not chapters or not (5 <= len(chapters) <= 8):
        print(f"   ↩️  Outline had {len(chapters or [])} chapter(s); 5-8 needed.")
        return None
    cast = [c for c in (data.get("cast") or [])
            if isinstance(c, dict) and c.get("name")][:MAX_CAST]
    places = [p for p in (data.get("places") or [])
              if isinstance(p, str) and p.strip()][:MAX_PLACES]
    chapters = fix_roles([c for c in chapters if c.get("title") and c.get("beats")])
    # Budgets scaled to the target, with floors so no chapter is a stub.
    raw = [max(80, int(c.get("words") or 0)) for c in chapters]
    scale = total_words / max(1, sum(raw))
    for c, w in zip(chapters, raw):
        c["words"] = max(90 if c["role"] == "open" else 180, int(w * scale))
        if c["role"] == "pivot":
            c["words"] = min(c["words"], 260)
        if c["role"] == "open":
            c["words"] = min(c["words"], 140)
    hooks = 0
    for c in chapters:
        c["hook"] = (c.get("hook") or "").strip()
        if c["hook"]:
            hooks += 1
            if hooks > 2 or c["role"] == "close":
                c["hook"] = ""
    return {"question": (data.get("question") or "").strip(), "cast": cast,
            "places": places, "chapters": chapters}


def print_plan(sheet, plan):
    print("=" * 70)
    print("STORY MODE — FACT SHEET")
    print(sheet_text(sheet))
    print("-" * 70)
    print(f"STORY QUESTION: {plan['question']}")
    print("CAST: " + "; ".join(f"{c['name']} ({c.get('role', '')})"
                               for c in plan.get("cast") or []))
    print("PLACES: " + ", ".join(plan.get("places") or []))
    for i, c in enumerate(plan["chapters"], 1):
        print(f"\n{i}. {c['title']}  [{c['role']}, ~{c['words']} words, "
              f"{c.get('span', '')}]  POV: {', '.join(c.get('pov') or [])}")
        for b in c["beats"]:
            print(f"   - {b}")
        if c["hook"]:
            print(f"   HOOK -> {c['hook']}")
    print("=" * 70)


# ---------------------------------------------------------------------------
# 4. Chapter briefs for the existing writer
# ---------------------------------------------------------------------------

def chapter_brief(plan, i, sheet):
    c = plan["chapters"][i]
    before = [f"{j + 1}. {x['title']}" for j, x in enumerate(plan["chapters"][:i])]
    after = [f"{j + 1}. {x['title']}" for j, x in enumerate(plan["chapters"])
             if j > i]
    beats = "\n".join(f"- {b}" for b in c["beats"])
    open_note = (
        "This is the COLD OPEN: drop the viewer into this moment in the first "
        "sentence — a time, a place, something happening — and stop before it "
        "resolves. The chapters after it go back in time and tell how it came "
        "to this.\n" if c["role"] == "open" else "")
    return (
        f"{open_note}"
        f"The video's story question: {plan['question']}\n"
        f"This chapter covers {c.get('span', '')}, following "
        f"{', '.join(c.get('pov') or ['its people'])}.\n"
        f"Tell these beats, in order, as scenes:\n{beats}\n"
        f"Chapters already told: {'; '.join(before) or 'none'}. "
        f"Still to come: {'; '.join(after) or 'none'} — do not tell their "
        "events here.\n"
        f"{_cast_note(plan)}\n"
        f"FACT SHEET (the only facts you may use):\n"
        f"{sheet_text(sheet, cast=plan.get('cast'))}")


def _cast_note(plan):
    cast, places = plan.get("cast") or [], plan.get("places") or []
    out = ""
    if cast:
        out += ("CAST — the ONLY people you may name, each introduced with "
                "their role: " + "; ".join(
                    f"{c['name']} ({c.get('role', '')})" for c in cast)
                + ". Anyone else is a role, never a name.\n")
    if places:
        out += ("PLACES — the only places you may name: " + ", ".join(places)
                + ". Describe any other place in plain words.\n")
    return out


def story_arc(plan, sheet):
    """[(heading, brief, words)] for the writer, plus per-chapter roles and
    hooks."""
    arc = [(c["title"], chapter_brief(plan, i, sheet), c["words"])
           for i, c in enumerate(plan["chapters"])]
    return arc, [c["role"] for c in plan["chapters"]], [c["hook"] for c in plan["chapters"]]


def save(build_dir, **parts):
    try:
        os.makedirs(build_dir, exist_ok=True)
        with open(os.path.join(build_dir, "story_plan.json"), "w",
                  encoding="utf-8") as f:
            json.dump(parts, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 5. Simplicity check — "could a viewer with no background follow this?"
# ---------------------------------------------------------------------------

LONG_SENTENCE_WORDS = 30
MAX_FIGURES_PER_CHAPTER = 4
_UNIT_RE = re.compile(r"\b(brigades?|divisions?|battalions?|regiments?|"
                      r"squadrons?|corps)\b", re.I)
_YEARISH = re.compile(r"^(1[5-9]\d\d|20[0-2]\d)$")
_MONTHS = ("january", "february", "march", "april", "may", "june", "july",
           "august", "september", "october", "november", "december")


def simplicity_issues(text, plan, sheet):
    """What a counting check finds in one chapter's narration: names outside
    the cast, places outside the list, unit names, too many or unrounded
    figures, over-long sentences. Plain sentences, ready to hand back to
    the writer."""
    issues = []
    low = text.lower()
    cast = plan.get("cast") or []
    if cast:
        extra = sorted({p["name"] for p in sheet.get("people") or []
                        if not _in_cast(p["name"], cast)
                        and re.search(rf"\b{re.escape(_surname(p['name']))}\b",
                                      low) and len(_surname(p["name"])) > 3})
        if extra:
            issues.append("Names people outside the cast: " + ", ".join(extra)
                          + ". Refer to them by their role instead.")
    places = [p.lower() for p in plan.get("places") or []]
    if places:
        extra = sorted({p for p in sheet.get("places") or []
                        if isinstance(p, str) and p.lower() not in places
                        and not any(p.lower() in q or q in p.lower() for q in places)
                        and re.search(rf"\b{re.escape(p.lower())}\b", low)})
        if extra:
            issues.append("Names places outside the list: " + ", ".join(extra)
                          + ". Describe them in plain words instead.")
    units = sorted({m.group(0).lower() for m in _UNIT_RE.finditer(text)})
    if units:
        issues.append("Uses military unit names (" + ", ".join(units)
                      + "). Say \"troops\", \"tanks\" or \"soldiers\" instead.")
    figures = []
    for m in re.finditer(r"\d[\d,.]*", text):
        tok = m.group(0).rstrip(".,")
        before = low[max(0, m.start() - 12):m.start()]
        after = low[m.end():m.end() + 12]
        if _YEARISH.match(tok) or any(mo in before or mo in after for mo in _MONTHS):
            continue          # a year or a date, not a figure
        if re.match(r"(st|nd|rd|th)\b", after):
            continue          # an ordinal ("7th"), caught by the unit rule
        if re.search(r"(a\.m|p\.m|o'clock|:)", after[:5]) or ":" in tok:
            continue          # a clock time
        figures.append(tok)
    if len(figures) > MAX_FIGURES_PER_CHAPTER:
        issues.append(f"Uses {len(figures)} figures ({', '.join(figures)}). "
                      f"Keep at most three, the ones that matter most.")
    unrounded = [f for f in figures
                 if len(f.replace(",", "")) >= 4 and f.replace(",", "")[-2:] != "00"]
    if unrounded:
        issues.append("Unrounded figures: " + ", ".join(unrounded)
                      + ". Round them (\"about 20,000\").")
    long = [x for x in re.split(r"(?<=[.!?])\s+", text)
            if len(x.split()) > LONG_SENTENCE_WORDS]
    if long:
        issues.append(f"{len(long)} sentence(s) over {LONG_SENTENCE_WORDS} "
                      f"words, e.g. \"{long[0][:90]}...\". Split them.")
    return issues


def viewer_check(pool, text, earlier, call):
    """A model reads the chapter as a viewer with no background who has
    watched only the chapters before it, and lists what would lose them.
    [] when clear, or when the check itself fails."""
    prompt = (
        "You are a regular YouTube viewer with NO knowledge of history, "
        "politics or the military. You have watched the video so far:\n"
        f"{earlier[-5000:] or '(nothing yet)'}\n\n"
        f"Now you hear this part:\n{text}\n\n"
        "List the points where you would get lost: a person you cannot place, "
        "a place you do not know why it matters, a term you do not "
        "understand, a jump you cannot follow, too many names or numbers at "
        "once. At most 5, each one short sentence naming the exact word or "
        "moment. Only real problems; an empty list if it is clear.\n"
        'Output ONLY JSON: {"confusing": [""]}')
    try:
        data = call(pool, "You judge whether a narration is easy to follow. "
                    "Output only JSON.", prompt, max_tokens=800)
    except Exception as e:
        if type(e).__name__ == "QuotaExhausted":
            raise
        return []
    items = (data or {}).get("confusing") if isinstance(data, dict) else None
    return [str(x).strip() for x in (items or []) if str(x).strip()][:5]
