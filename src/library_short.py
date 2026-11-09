"""
Option 3 (handbook): the history half of a Short is a library entry made once
(library/<slug>/entry.json); each day only the "today" half is written, by
the free model, and checked here by code.

  load()                    all entries
  candidates(heads, ...)    (headline, entry) pairs: code matches actors
  today_problems(...)       checks of the model's today half
  build(...)                the owner folder paradox_short.py renders
"""

import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.join(HERE, "..", "library")
WORDS = (85, 150)                     # spoken words in all
COOLDOWN_DAYS = 45                    # the same entry is not posted twice inside this
NUMBER_WORDS = ("two three four five six seven eight nine ten eleven twelve twenty thirty "
                "forty fifty sixty seventy eighty ninety hundred thousand million billion "
                "dozens hundreds thousands millions billions half double triple").split()
STOP = set("""a an the and or but of in on at to for from by with as is was were be been this that
these those it its his her their they them he she we you our who which what when where why how not no
all after before into over under than then there here week year years today now said says""".split())
ALLOWED_CAPS = {"this", "today", "now", "but", "the", "in", "on", "so", "and", "a", "it", "its", "he",
                "she", "they", "his", "her", "their", "that", "with", "as", "at", "for", "when"}


def load(root=LIB):
    out = []
    if not os.path.isdir(root):
        return out
    for slug in sorted(os.listdir(root)):
        p = os.path.join(root, slug, "entry.json")
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as f:
                e = json.load(f)
            e.setdefault("slug", slug)
            out.append(e)
    return out


def validate(e):
    """Problems with a library entry (empty = usable)."""
    out = []
    for k in ("slug", "event", "then_year", "actors", "images", "lines", "then", "now_people",
              "end_ideas", "examples", "facts"):
        if not e.get(k):
            out.append(f"{e.get('slug')}: missing {k}")
    shown = set()
    for l in e.get("lines") or []:
        for k in (l.get("show"), l.get("also")):
            if k:
                shown.add(k)
                if k not in (e.get("images") or {}):
                    out.append(f"{e.get('slug')}: line shows unknown image {k!r}")
        if not l.get("say") or not l.get("tag"):
            out.append(f"{e.get('slug')}: a picture line needs say and tag")
    for k, v in (e.get("images") or {}).items():
        if not (v.get("url") or v.get("video")) or not v.get("credit"):
            out.append(f"{e.get('slug')}: image {k} needs url and credit")
    if len(e.get("lines") or []) < 4:
        out.append(f"{e.get('slug')}: fewer than 4 picture lines")
    return out


def _has(text, phrase):
    return re.search(r"(?<![A-Za-z])" + re.escape(phrase.lower()) + r"(?![A-Za-z])", text.lower()) is not None


def match_score(headline, entry):
    return sum(1 for a in entry.get("actors") or [] if _has(headline, a))


def candidates(heads, entries, recent=(), max_per_head=2):
    """(headline, entry) pairs, the biggest headline first. recent = slugs on
    cooldown. Code only: no model call."""
    out = []
    for h in heads:
        scored = sorted(((match_score(h, e), e) for e in entries if e["slug"] not in recent
                         and not validate(e)), key=lambda p: -p[0])
        for s, e in scored[:max_per_head]:
            if s > 0:
                out.append((h, e))
    return out


# ---------------------------------------------------------------- the today half

def _spoken(spec, entry):
    hist = sum(len(l["say"].split()) for l in entry["lines"])
    today = sum(len((spec.get(k) or "").split()) for k in ("line1", "line2", "now", "question"))
    return hist + today


def _unknown_names(text, known_text):
    known = set(re.findall(r"[a-z0-9]+", known_text.lower()))
    bad = []
    for m in re.finditer(r"\b[A-Z][a-zA-Z'’-]{2,}", text):
        w = re.sub(r"['’]s$", "", m.group(0)).lower()
        if w not in known and w not in ALLOWED_CAPS:
            bad.append(m.group(0))
    return bad


def _invented_numbers(text, headline):
    head = headline.lower()
    bad = []
    for tok in re.findall(r"[A-Za-z0-9][A-Za-z0-9,.%-]*", text):
        t = tok.lower().strip(".,-")
        if (re.fullmatch(r"\d[\d,.%]*", t) or t in NUMBER_WORDS) and t not in head:
            bad.append(tok)
    return bad


def today_problems(spec, headline, entry):
    """Why the model's today half cannot be used (empty list = fine)."""
    out = []
    for k in ("line1", "line2", "now", "now_tag", "question", "end_title", "title", "description",
              "now_person"):
        if spec.get(k) in (None, ""):
            out.append(f"missing {k}")
    if out:
        return out
    l1, l2, now, q = (spec[k].strip() for k in ("line1", "line2", "now", "question"))
    if not re.match(r"(This week|Today|Tonight|This morning|Overnight),", l1):
        out.append('line1 must start "This week," or "Today,"')
    if len(l1.split()) > 32:
        out.append("line1 must be at most 32 words")
    if str(entry["then_year"]) not in l2 and not re.search(r"\b(before|history)\b", l2.lower()):
        out.append(f"line2 must turn to the past (say {entry['then_year']})")
    if len(l2.split()) > 24:
        out.append("line2 must be at most 24 words")
    if len(now.split()) > 26:
        out.append("now line must be at most 26 words")
    if not q.endswith("?") or len(q.split()) > 24:
        out.append("question must end with ? and be at most 24 words")
    if not 1 <= len(spec["end_title"]) <= 18 or spec["end_title"] != spec["end_title"].upper():
        out.append("end_title must be 1-18 characters, capitals")
    if len(spec["now_tag"]) > 24:
        out.append("now_tag must be at most 24 characters")
    t = spec["title"].strip()
    if len(t) > 95 or not t.endswith("#shorts"):
        out.append('title must be under 95 characters and end "#shorts"')
    people = entry["now_people"]
    if not isinstance(spec["now_person"], int) or not 0 <= spec["now_person"] < len(people):
        out.append(f"now_person must be 0..{len(people) - 1}")
    n = _spoken(spec, entry)
    if not WORDS[0] <= n <= WORDS[1]:
        out.append(f"the Short must have {WORDS[0]}-{WORDS[1]} spoken words, not {n}")
    # today's lines only say what the headline says (+ the history, for the turn)
    for name, text, known in (("line1", l1, headline), ("now", now, headline + " " + entry["facts"]),
                              ("title", t, headline + " " + entry["facts"] + " " + entry["event"])):
        for w in (_unknown_names(text, known) if name != "title" else []):  # titles are Title Case
            out.append(f"the name {w!r} in {name} is not in the headline")
        for w in _invented_numbers(text, headline + (" " + entry["facts"] if name != "line1" else "")):
            out.append(f"the number {w!r} in {name} is not in the headline")
    for w in _invented_numbers(l2, str(entry["then_year"]) + " " + entry["facts"] + " " + entry["event"]):
        out.append(f"the number {w!r} in line2 is not in the history")
    return out


def prompt(headline, entry, problems=()):
    ex = entry["examples"]
    hist = "\n".join(f"- {l['tag']}: {l['say']}" for l in entry["lines"])
    people = "\n".join(f"{i}. {p['article']}" for i, p in enumerate(entry["now_people"]))
    return (
        "You write the TODAY half of a 45-second history Short. The history half is done.\n\n"
        f"TODAY'S HEADLINE (the only source for today): {headline}\n\n"
        f"THE HISTORY (fixed, the Short shows it after your line 2): {entry['event']} ({entry['then_year']})\n"
        f"{hist}\n\n"
        "A GOOD EXAMPLE of the four lines, for another story:\n"
        f"line1: {ex['line1']}\nline2: {ex['line2']}\nnow: {ex['now']}\nquestion: {ex['question']}\n\n"
        "RULES:\n"
        "- First decide: is this history a direct parallel of the headline (same country or the "
        "same actors, the same kind of act)? If not, answer {\"fit\": false, \"why\": \"...\"}.\n"
        "- line1: today's news in one sentence, starting \"This week,\" or \"Today,\". Only what the "
        "headline says: no name, number or detail that is not in it.\n"
        f"- line2: the turn to the past, naming {entry['then_year']} (\"But in {entry['then_year']}, ...\"), "
        "at most 22 words, using only facts from THE HISTORY.\n"
        "- now: one sentence back to today. No new number or name.\n"
        "- question: one real question for the comments, about then and now.\n"
        "- end_title: that question in 1-4 words, capitals (e.g. TRUST IT AGAIN?).\n"
        "- now_tag: 1-3 words in capitals for the picture of today (e.g. ABU DHABI · 2026).\n"
        f"- now_person: the number of the person whose photo shows today's news:\n{people}\n"
        "- title: today's news, then what happened last time, under 95 characters, ending \" #shorts\".\n"
        "- description: 2-3 plain sentences: the news, then the history.\n"
        "- tags: 6-8 short tags.\n"
        "Plain spoken English, short sentences. Numbers under 100 in words, years as digits.\n"
        'Answer JSON only: {"fit": true, "line1": "", "line2": "", "now": "", "question": "", '
        '"end_title": "", "now_tag": "", "now_person": 0, "title": "", "description": "", "tags": []}\n'
        + (("\nYOUR LAST ANSWER WAS REJECTED:\n- " + "\n- ".join(problems) + "\nFix every point.\n")
           if problems else ""))


# ---------------------------------------------------------------- the folder

def build(entry, spec, now_credit, now_url):
    """(short.json, sources.json, short_meta.json) dicts for paradox_short.py."""
    person = entry["now_people"][spec["now_person"]]
    end = {"title": spec["end_title"], "sub": (entry["end_ideas"][0]["sub"]),
           "pointer": "TELL US IN THE COMMENTS", "arrow": "right"}
    lines = ([{"show": "opening", "say": spec["line1"].strip()},
              {"show": "opening", "say": spec["line2"].strip()}]
             + [dict(l) for l in entry["lines"]]
             + [{"show": "now", "tag": spec["now_tag"].strip(), "say": spec["now"].strip()},
                {"show": "end", "say": spec["question"].strip()}])
    short = {"then_year": entry["then_year"], "music": entry.get("music", "Beyond the Lows"),
             "now_tag": "NOW", "now_focus": person.get("focus", [0.5, 0.3]),
             "then_focus": entry.get("then_focus", [0.5, 0.45]),
             "images": entry["images"], "lines": lines, "end": end}
    sources = {"now": {"url": now_url, "credit": now_credit}, "then": entry["then"]}
    credits = list(dict.fromkeys([now_credit, entry["then"]["credit"]]
                                 + [v["credit"] for v in entry["images"].values()]))
    tags = [t for t in spec.get("tags") or [] if isinstance(t, str)][:10] or ["history", "shorts"]
    desc = (spec["description"].strip() + "\n\nPictures and footage (via Wikimedia Commons):\n"
            + "\n".join(f"• {c}" for c in credits) + "\n\n#shorts #history")
    meta = {"title": spec["title"].strip(), "description": desc[:4900], "tags": tags,
            "comment": spec["question"].strip() + f" {entry['event']}."}
    return short, sources, meta
