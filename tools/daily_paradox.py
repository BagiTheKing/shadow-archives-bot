"""
Five NOW / THEN Shorts a week, each one sent to the owner before it is
posted (owner, 5 Nov: "one short for every day, no long form videos at all,
unless there is a big event"; agreed: 5 a week, with his review).

The two Shorts that worked (Israel nuclear, 1.9K views; Tanker War, 1.1K)
were written and pictured by hand; the automatic "Catastrophe Files" Shorts
got 30-400. This does the hand work the same way, one step at a time:

  1. the story: this week's geopolitical headlines (trend_source.py), and a
     settled event before 2010 that explains one of them, with its own
     Wikipedia article; never a subject the channel has already posted
  2. the facts: that article's fact sheet (shorts_facts.py), and every
     figure, name and quote of the story lines checked against it
  3. the lines: today in one line, "But in <year>, ..." in the next, the
     story in four to six lines, back to today, and a question for the
     comments (no long video to send anyone to any more)
  4. the pictures: one real photo per line from the article, its Commons
     category and searches, chosen by the words of the line; nothing with a
     meaningless title, nothing far newer than the story
  5. tools/paradox_short.py renders it from the folder this writes

    python tools/daily_paradox.py out_dir

Writes out_dir/story/ (short.json, sources.json: an owner folder for
paradox_short.py), out_dir/short_meta.json (title, description, tags, first
comment) and out_dir/pick.json (the news, the event, why).
"""

import datetime as dt
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import archive_engine as ae  # noqa: E402

YEAR = dt.date.today().year
CUTOFF = int(os.getenv("HISTORY_CUTOFF_YEAR", "2010"))
WORDS = (95, 125)                  # the two hits: 108 and 116 spoken words
JUNK = re.compile(r"\bflag\b|\blogo\b|\bseal of\b|coat of arms|\bribbon\b|insignia|"
                  r"\bmedal\b|\bemblem\b|\bsignature\b|\bicon\b|\bmap\b|locator|"
                  r"\bstamp\b|\bchart\b|\bdiagram\b", re.I)
STOP = set("""a an the and or but of in on at to for from by with as is was were be been
this that these those it its his her their they them he she we you our who which what
when where why how not no all one two three after before into over under than then
there here week year years today now same again would could will said says""".split())

FORMAT = """\
THE FORMAT (two of these did 1,100 and 1,900 views; follow it exactly):
{"lines": [
  {"show": "opening", "say": "This week, Iran's Revolutionary Guard reported three tankers on fire in the Strait of Hormuz."},
  {"show": "opening", "say": "But in 1987, in these same waters, tankers dragged America into a war with Iran."},
  {"show": "picture", "tag": "1987", "picture": "reflagged Kuwaiti tankers convoy", "say": "Kuwait put its tankers under the American flag, and US warships escorted them through the Gulf."},
  {"show": "picture", "tag": "1987", "picture": "Iran Ajr mines", "say": "Mines hit the very first convoy. American helicopters caught an Iranian ship laying more."},
  {"show": "picture", "tag": "APRIL 1988", "picture": "Sahand frigate burning", "say": "In April 1988, the US Navy sank or crippled half of Iran's working navy in a single day."},
  {"show": "picture", "tag": "JULY 1988", "picture": "USS Vincennes", "say": "Three months later, the cruiser USS Vincennes shot down an Iranian airliner."},
  {"show": "picture", "tag": "JULY 1988", "picture": "Iran Air Flight 655 mourners", "say": "All two hundred and ninety people on board were killed."},
  {"show": "now", "tag": "SAME STRAIT", "say": "Now the tankers are burning again, in the same waters."},
  {"show": "end", "say": "Who do you think fired first, then and now?"}
 ],
 "end": {"title": "WHO FIRED FIRST?", "sub": "THE TANKER WAR, 1987"},
 "title": "Tankers Burn in Hormuz Again. In 1987 It Dragged America Into War #shorts",
 "description": "Two or three sentences: this week's news, then what happened in the past.",
 "tags": ["Tanker War", "Strait of Hormuz", "Iran", "history", "shorts"],
 "comment": "The same question as the last line, for the comments."}
"""


class Skip(Exception):
    """This pair cannot make a good Short: the next one is tried."""


MIN_PICTURES = 6                    # a story with fewer real photos is skipped


def _years_in(c):
    """Every year a picture says it is from: its date, and the years in its
    title (including dates written 20041101)."""
    t = c.get("title") or ""
    ys = [int(y) for y in re.findall(r"(?<!\d)(1[6-9]\d\d|20[0-4]\d)(?!\d)", t)]
    ys += [int(m) for m in re.findall(r"(?<!\d)((?:19|20)\d\d)(?:0[1-9]|1[0-2])(?:[0-3]\d)(?!\d)", t)]
    if c.get("year"):
        ys.append(int(c["year"]))
    return ys


def era_ok(c, lo, hi):
    """A picture of the past from the past: nothing dated outside lo..hi
    (a 2004 photo of the Sorbonne for 1968, de Gaulle in 1942 for 1968)."""
    return all(lo <= y <= hi for y in _years_in(c))


def _words(text):
    return [w for w in re.findall(r"[a-z0-9]+", (text or "").lower())
            if w not in STOP and len(w) > 2]


# ---------------------------------------------------------------- 1. the story

def headlines():
    """This week's world headlines, the most searched first."""
    import trend_source as ts
    try:
        _, ranked = ts.get_trending_subjects(limit=14)
    except Exception as e:
        print(f"⚠️ Trends unavailable ({type(e).__name__}: {e}).")
        ranked = []
    heads = [r["subject"] for r in ranked]
    try:
        for h in ts.fetch_headlines(("US", "GB"), ("WORLD",)):
            if h not in heads and ts._geopolitical(h) and not ts._excluded(h):
                heads.append(h)
    except Exception as e:
        print(f"⚠️ Headlines unavailable ({type(e).__name__}: {e}).")
    return heads[:30]


def recent():
    import published_history as ph
    videos = ph.load_published(limit=60) or []
    return ph.recent_titles(videos, limit=40)


def pick(pool, call, heads, titles, avoid=()):
    system = ("You choose the story for a 45-second history Short on a channel whose "
              "every video starts from this week's news and shows the moment in "
              "history that explains it. You answer with JSON only.")
    user = (
        "THIS WEEK'S HEADLINES:\n" + "\n".join(f"- {h}" for h in heads) + "\n\n"
        "ALREADY POSTED (never the same subject or the same historical event):\n"
        + "\n".join(f"- {t}" for t in titles) + "\n\n"
        + (("ALSO RULED OUT TODAY: " + "; ".join(avoid) + "\n\n") if avoid else "")
        + "Choose up to 3 pairs, best first. Each pair is ONE headline above and ONE "
        f"settled historical event before {CUTOFF} that is its direct parallel: the "
        "same place, the same countries or the same kind of act, so that someone who "
        "sees the headline understands it better after the Short. Not a loose theme. "
        "The event needs its own English Wikipedia article and must be well "
        "photographed (roughly after 1860, and covered by news photographers or "
        "the military: a Short needs six or more real photos). Prefer events most viewers have never "
        "heard of over the famous ones, and wars, coups, crises and disasters over "
        "elections and summits.\n"
        'Answer: {"picks": [{"headline": "<copied from the list>", '
        '"news": "<what happened this week, in one plain sentence, only what the '
        'headline says>", "event": "<the event\'s name>", "article": "<its exact '
        'English Wikipedia article title>", "year": <the year it starts>, "end_year": <the year it ends>, '
        '"now_article": "<a DIFFERENT English Wikipedia article, about today: the '
        'leader in this week\'s news, the ship, the city; its main photo opens the '
        'Short as NOW, so never the historical event\'s article>", "why": "<one sentence>"}]}')
    data = call(pool, system, user, 6000, expect={"picks": list})
    out = []
    for p in (data or {}).get("picks") or []:
        try:
            year = int(p.get("year"))
        except (TypeError, ValueError):
            continue
        try:
            end = max(year, min(int(p.get("end_year") or year), year + 15))
        except (TypeError, ValueError):
            end = year
        if year < CUTOFF and p.get("article") and p.get("headline") and p.get("now_article"):
            out.append(dict(p, year=year, end_year=end))
    return out


# ---------------------------------------------------------------- 3. the lines

def _problems(spec, facts, news):
    import shorts_facts as sf
    lines = spec.get("lines") or []
    shows = [l.get("show") for l in lines]
    out = []
    if len(lines) < 7 or len(lines) > 10:
        out.append(f"there must be 7 to 10 lines, not {len(lines)}")
    if shows[:2] != ["opening", "opening"] or shows[-2:] != ["now", "end"] \
            or any(s != "picture" for s in shows[2:-2]):
        out.append("the lines must be: opening, opening, 4 to 6 picture lines, now, end")
    if lines and not lines[0].get("say", "").startswith("This week"):
        out.append('line 1 must start "This week,"')
    if len(lines) > 1 and not re.match(r"But (in|on|back in) ", lines[1].get("say", "")):
        out.append('line 2 must start "But in <year>,"')
    n = sum(len((l.get("say") or "").split()) for l in lines)
    if not WORDS[0] <= n <= WORDS[1]:
        out.append(f"the lines must have {WORDS[0]}-{WORDS[1]} spoken words, not {n}")
    for l in lines[2:-2]:
        if not l.get("picture") or not l.get("tag"):
            out.append(f"the picture line {l.get('say', '')[:40]!r} needs a tag and a picture")
    end = spec.get("end") or {}
    if not end.get("title") or len(end["title"]) > 18:
        out.append("end.title must be 1-18 characters")
    if not end.get("sub") or len(end["sub"]) > 40:
        out.append("end.sub must be 1-40 characters")
    if not spec.get("title") or len(spec["title"]) > 95:
        out.append("the title must be under 95 characters")
    # the history is checked against its article; today against the headline
    story = {"scenes": [{"narration": l.get("say", "")} for l in lines[1:-2]]}
    out += sf.unsupported_claims(story, facts["text"])
    today = " ".join(l.get("say", "") for l in (lines[:1] + lines[-2:-1]))
    known = (news + " " + facts["text"]).lower()
    for m in re.finditer(r"\b[A-Z][a-zA-Z'’-]{2,}", today):
        w = re.sub(r"['’]s$", "", m.group(0)).lower()
        if w not in known and w not in ("this", "now", "but", "week"):
            out.append(f"the name {m.group(0)!r} in today's lines is not in the headline")
    return out


def write(pool, call, p, facts):
    import shorts_facts as sf
    import story_mode as sm
    system = ("You write 45-second history Shorts. Plain spoken English, short "
              "sentences, no adjectives the facts do not need. You answer with JSON only.")
    news = f"{p['headline']} / {p['news']}"
    base = (
        f"THIS WEEK: {news}\n"
        f"THE PAST: {p['event']} ({p['year']})\n\n"
        f"{sf.prompt_block(facts)}\n{FORMAT}\n"
        "RULES:\n"
        "- line 1 says this week's news, nothing the headline does not say;\n"
        f"- line 2 starts \"But in {p['year']},\" and names what happened then;\n"
        "- 4 to 6 picture lines tell the story in order, each one fact from the sheet; "
        "\"tag\" is the year or month and year on screen; \"picture\" is 2-5 words a "
        "real photo of that moment would have in its file name (a person, a ship, a "
        "place, an event), never an idea;\n"
        "- the now line goes back to this week in one sentence;\n"
        "- the end line asks the viewer one real question about then and now, "
        "the kind people answer in the comments; end.title is that question in "
        "1-4 words in capitals, end.sub names the event and year in capitals;\n"
        f"- {WORDS[0]}-{WORDS[1]} spoken words in all; numbers under 100 in words; "
        "years as digits;\n"
        "- the title: this week's news, then what happened last time, in the "
        "format's style, ending \" #shorts\".\n")
    problems, spec = [], None
    for attempt in range(4):
        user = base + (("\nYOUR LAST ANSWER WAS REJECTED:\n- " + "\n- ".join(problems)
                        + "\nFix every point and answer again.\n") if problems else "")
        spec = call(pool, system, user, 8000, expect={"lines": ("list", 7), "end": dict, "title": str})
        if not spec:
            continue
        problems = _problems(spec, facts, news)
        if not problems:
            return spec
        print(f"   ↩️  Draft {attempt + 1} rejected: " + "; ".join(problems)[:400])
    return None


# ---------------------------------------------------------------- 4. the pictures

def picture_pool(p, facts):
    pool, seen = [], set()
    lo, hi = p["year"] - 5, p.get("end_year", p["year"]) + 5
    story = set(_words(p["event"] + " " + p["article"] + " " + facts["title"])) | {str(p["year"])}

    def add(cands, searched=False):
        for c in cands or []:
            if not c or not c.get("url") or c["url"] in seen:
                continue
            t = c.get("title") or ""
            if JUNK.search(t) or not ae.meaningful(t):
                continue
            if (c.get("width") or 9999) < 640:
                continue
            if not era_ok(c, lo, hi):
                continue
            # a search finds anything with the same words: a 1919 Seattle
            # strike for "general strike" in France, 1968
            if searched and not (story & set(_words(t))):
                continue
            seen.add(c["url"])
            pool.append(c)
    for art in dict.fromkeys([facts["title"], p["article"]]):
        add([ae.article_lead_image(art)])
        add(ae.article_images(art))
        cat = ae.commons_category_of(art)
        if cat:
            add(ae.category_images(cat))
    # the people in the story: the photo at the top of each one's article
    for person in (facts["sheet"].get("people") or [])[:8]:
        add([ae.article_lead_image(person.get("name", ""))])
    add(ae.search_commons(p["event"], limit=15), searched=True)
    return pool, add


def choose(cands, want, used, latest):
    words = set(_words(want))
    best, score = None, -1.0
    for i, c in enumerate(cands):
        if c["url"] in used:
            continue
        hit = len(words & set(_words(c.get("title"))))
        s = hit - i * 0.001               # the article's own order breaks ties
        if s > score:
            best, score = c, s
    return best, score


def pictures(p, spec, facts, found):
    pool, add = found
    lines = spec["lines"]
    years = [int(y) for l in lines for y in re.findall(r"\b(1[5-9]\d\d|20[01]\d)\b",
                                                        l.get("tag", "") + " " + l.get("say", ""))]
    latest = max(years + [p["year"]])
    used, images = set(), {}
    # THEN: the opening's past, the event as a whole
    then, _ = choose(pool, p["event"] + " " + lines[1]["say"], used, latest)
    for k, l in enumerate(lines[2:-2]):
        want = l["picture"] + " " + l["say"]
        c, s = choose(pool, want, used | ({then["url"]} if then else set()), latest)
        if s < 1:
            # nothing in the article's pictures names it: search Commons for it
            add(ae.search_commons(l["picture"], limit=12), searched=True)
            c, s = choose(pool, want, used | ({then["url"]} if then else set()), latest)
        if not c:
            raise Skip(f"no picture for {l['say'][:60]!r}")
        used.add(c["url"])
        key = f"p{k}"
        images[key] = {"url": c["url"], "credit": ae.format_credit(c), "title": c.get("title")}
        l["show"] = key
        print(f"   {key}: {c.get('title', '')[:70]}  ←  {l['picture']}")
    if not then:
        then = next((c for c in pool if c["url"] not in used), None)
    if not then:
        raise Skip("no picture of the past for the opening")
    # NOW: a photo of today's story, never one of the past (the 1968 crowd
    # was labelled NOW): another article than the past's, and nothing dated
    # more than a few years ago
    taken = used | {then["url"]}

    def recent(c):
        return c and c.get("url") not in taken and not JUNK.search(c.get("title") or "") \
            and era_ok(c, YEAR - 6, YEAR)
    now = None
    if p["now_article"].strip().lower() not in (p["article"].lower(), facts["title"].lower()):
        lead = ae.article_lead_image(p["now_article"])
        now = lead if recent(lead) else None
        if not now:
            now = next((c for c in ae.search_commons(p["now_article"], limit=15) if recent(c)), None)
    if not now:
        raise Skip(f"no recent NOW picture for {p['now_article']!r}")
    print(f"   now: {now.get('title', '')[:70]}\n   then: {then.get('title', '')[:70]}")
    sources = {"now": {"url": now["url"], "credit": ae.format_credit(now)},
               "then": {"url": then["url"], "credit": ae.format_credit(then)}}
    return images, sources


# ---------------------------------------------------------------- the Short

def main(out):
    from llm_provider import get_pool
    from longform_llm import _call
    import shorts_facts as sf
    pool = get_pool()
    heads = headlines()
    if not heads:
        raise SystemExit("❌ No headlines this week")
    titles = recent()
    import time
    tried = []
    for attempt in range(3):
        if attempt:
            # Gemini answers 503 when it is overloaded: it passes in minutes
            print("   ⏳ Waiting 2 minutes before asking again...")
            time.sleep(120)
        picks = pick(pool, _call, heads, titles, avoid=tried)
        if not picks:
            print("   ↩️  No pair came back.")
        for p in picks:
            tried.append(p["event"])
            print(f"📰 {p['headline'][:90]}\n🕰️  {p['event']} ({p['year']}): {p.get('why', '')[:120]}")
            facts = sf.research_short(p["article"])
            if not facts:
                print("   ↩️  No usable article: next pair.")
                continue
            # the pictures first: a story without real photos is not written
            found = picture_pool(p, facts)
            print(f"🖼️  {len(found[0])} pictures for the story")
            if len(found[0]) < MIN_PICTURES:
                print(f"   ↩️  Fewer than {MIN_PICTURES} real pictures: next pair.")
                continue
            spec = write(pool, _call, p, facts)
            if not spec:
                print("   ↩️  No draft passed the checks: next pair.")
                continue
            try:
                images, sources = pictures(p, spec, facts, found)
            except Skip as e:
                print(f"   ↩️  {e}: next pair.")
                continue
            return save(out, p, spec, facts, images, sources)
    raise SystemExit("❌ No story passed today")


def save(out, p, spec, facts, images, sources):
    story = os.path.join(out, "story")
    os.makedirs(story, exist_ok=True)
    lines = [{k: v for k, v in l.items() if k != "picture"} for l in spec["lines"]]
    short = {"then_year": p["year"], "music": "Beyond the Lows", "now_tag": "NOW",
             "images": {k: {"url": v["url"], "credit": v["credit"]} for k, v in images.items()},
             "lines": lines,
             "end": {"title": spec["end"]["title"], "sub": spec["end"]["sub"],
                     "pointer": "TELL US IN THE COMMENTS", "arrow": "right"}}
    with open(os.path.join(story, "short.json"), "w", encoding="utf-8") as f:
        json.dump(short, f, ensure_ascii=False, indent=1)
    with open(os.path.join(story, "sources.json"), "w", encoding="utf-8") as f:
        json.dump(sources, f, ensure_ascii=False, indent=1)
    credits = [sources["now"]["credit"], sources["then"]["credit"]] + \
        [v["credit"] for v in images.values()]
    credits = list(dict.fromkeys(credits))
    desc = (spec.get("description", "").strip() + "\n\n"
            f"Source: Wikipedia, \"{facts['title']}\": {facts['url']}\n\n"
            "Pictures (via Wikimedia Commons):\n" + "\n".join(f"• {c}" for c in credits)
            + "\n\n#shorts #history")
    tags = [t for t in spec.get("tags") or [] if isinstance(t, str)][:12]
    meta = {"title": spec["title"].strip(), "description": desc[:4900],
            "tags": tags or ["history", "shorts"],
            "comment": spec.get("comment") or spec["lines"][-1]["say"]}
    with open(os.path.join(out, "short_meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    with open(os.path.join(out, "pick.json"), "w", encoding="utf-8") as f:
        json.dump({k: p[k] for k in ("headline", "news", "event", "article", "year",
                                     "now_article", "why") if k in p}
                  | {"pictures": {k: v["title"] for k, v in images.items()}},
                  f, ensure_ascii=False, indent=1)
    print(f"✅ {meta['title']}")
    for l in spec["lines"]:
        print(f"   [{l.get('show')}] {l['say']}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "out")
