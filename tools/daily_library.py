"""
The daily Short from the history library (handbook, Option 3).

  1. today's headlines (the biggest first), matched by code to library entries
  2. the free model writes only the today half (src/library_short.py checks it)
  3. NOW = the lead photo of the person it names, checked recent
  4. writes out/story/ (short.json, sources.json), out/short_meta.json and
     out/pick.json; with nothing to post it writes out/skip.json instead

    python tools/daily_library.py out
"""

import datetime as dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
sys.path.insert(0, HERE)

import library_short as L  # noqa: E402

YEAR = dt.date.today().year
LOG = os.path.join(HERE, "..", "library", "log.json")


def read_log():
    try:
        with open(LOG, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return []


def on_cooldown(log, today=None):
    today = today or dt.date.today()
    out = set()
    for r in log:
        if r.get("status") != "posted":
            continue
        try:
            age = (today - dt.date.fromisoformat(r["date"])).days
        except (KeyError, ValueError):
            continue
        if age < L.COOLDOWN_DAYS:
            out.add(r.get("slug"))
    return out


def now_picture(entry, spec):
    """The lead photo of the named person, recent and of that person; the
    next person of the entry if not."""
    import archive_engine as ae
    import daily_paradox as dp
    order = [spec["now_person"]] + [i for i in range(len(entry["now_people"])) if i != spec["now_person"]]
    for i in order:
        art = entry["now_people"][i]["article"]
        who = set(w for w in dp._words(art) if len(w) > 3)

        def ok(c):
            return c and c.get("url") and not dp.JUNK.search(c.get("title") or "") \
                and dp.era_ok(c, YEAR - 5, YEAR) and bool(who & set(dp._words(c.get("title"))))
        lead = ae.article_lead_image(art)
        pic = lead if ok(lead) else next((c for c in ae.search_commons(art, limit=15) if ok(c)), None)
        if pic:
            return i, pic, ae.format_credit(pic)
    return None, None, None


def write_today(pool, call, headline, entry):
    problems = []
    for attempt in range(2):
        spec = call(pool, "You write the 'today' half of history Shorts. You answer with JSON only.",
                    L.prompt(headline, entry, problems), 3000, expect={"fit": bool})
        if not spec:
            continue
        if spec.get("fit") is False:
            print(f"   ↩️  Not a fit: {str(spec.get('why'))[:120]}")
            return None
        problems = L.today_problems(spec, headline, entry)
        if not problems:
            return spec
        print(f"   ↩️  Draft {attempt + 1} rejected: " + "; ".join(problems)[:400])
    return None


def main(out):
    os.makedirs(out, exist_ok=True)

    def skip(reason, **kw):
        print(f"⏭️  Nothing posted: {reason}")
        with open(os.path.join(out, "skip.json"), "w", encoding="utf-8") as f:
            json.dump({"reason": reason, **kw}, f, ensure_ascii=False, indent=1)

    entries = L.load()
    bad = [p for e in entries for p in L.validate(e)]
    if bad:
        print("⚠️ Library problems: " + "; ".join(bad))
    import daily_paradox as dp
    heads = dp.headlines()
    if not heads:
        return skip("no headlines")
    log = read_log()
    cands = L.candidates(heads, entries, recent=on_cooldown(log))
    print(f"📰 {len(heads)} headlines, {len(cands)} matches with the library")
    if not cands:
        return skip("no headline matches a library entry", headlines=heads[:10])
    from llm_provider import get_pool
    from longform_llm import _call
    pool = get_pool()
    for headline, entry in cands[:4]:
        print(f"🕰️  {headline[:90]}  →  {entry['slug']}")
        spec = write_today(pool, _call, headline, entry)
        if not spec:
            continue
        i, pic, credit = now_picture(entry, spec)
        if not pic:
            print("   ↩️  No recent NOW photo: next match.")
            continue
        spec["now_person"] = i
        short, sources, meta = L.build(entry, spec, credit, pic["url"])
        story = os.path.join(out, "story")
        os.makedirs(story, exist_ok=True)
        for name, data, d in (("short.json", short, story), ("sources.json", sources, story),
                              ("short_meta.json", meta, out)):
            with open(os.path.join(d, name), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
        with open(os.path.join(out, "pick.json"), "w", encoding="utf-8") as f:
            json.dump({"date": dt.date.today().isoformat(), "slug": entry["slug"],
                       "headline": headline, "title": meta["title"], "now": pic.get("title")},
                      f, ensure_ascii=False, indent=1)
        print(f"✅ {meta['title']}")
        for l in short["lines"]:
            print(f"   [{l['show']}] {l['say']}")
        return
    skip("no match passed the checks", headlines=heads[:10])


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "out")
