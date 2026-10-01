"""
The checklist every long-form video passes before it is rendered and again
before it is uploaded (docs/VIDEO_SPEC.md). Whatever can be repaired without
a model is repaired here; what cannot is reported in the run's summary, and
only a video that would go up broken is stopped.

    script_check(script, story, scenes) -> list of items   (before voice/render)
    upload_check(title, description, thumb_path, variants) -> list of items

An item is (ok, name, note). ok is True, "fixed", False (reported) or
"block" (the run stops).
"""

import os
import re


def _real(text):
    bare = (text or "").replace("...", " ").replace("…", " ")
    return len(re.findall(r"[A-Za-z]", bare)) >= 3


def _quote_text(e):
    return " ".join((e or {}).get("sentences") or [(e or {}).get("text") or ""]).strip()


def _fallback_title(topic):
    t = re.split(r"[,:;—]", topic or "")[0].strip()
    return t[:70].rsplit(" ", 1)[0] if len(t) > 70 else t


def script_check(script, story=None, scenes=()):
    from longform_llm import stock_epigraph
    items = []
    chapters = script.get("chapters") or []

    title = (script.get("title") or "").strip()
    if not _real(title) or len(title) > 100:
        script["title"] = _fallback_title(script.get("topic"))
        items.append(("fixed", "Title", f"{title[:40]!r} replaced by {script['title']!r}"))
    else:
        items.append((True, "Title", title))

    desc = script.get("description") or ""
    kept = [ln for ln in desc.splitlines() if not ln.strip() or _real(ln)]
    if len(kept) != len(desc.splitlines()):
        script["description"] = "\n".join(kept).strip()
        items.append(("fixed", "Description", "placeholder lines removed"))
    elif not _real(desc):
        items.append((False, "Description", "no written summary (topic line only)"))
    else:
        items.append((True, "Description", f"{len(desc)} characters"))

    q = script.get("comment_question") or ""
    if q and not _real(q):
        script["comment_question"] = ""
        items.append(("fixed", "Closing question", "placeholder dropped"))

    topic = script.get("topic") or ""
    if not _real(_quote_text(script.get("intro_epigraph"))):
        script["intro_epigraph"] = stock_epigraph(topic)
        items.append(("fixed", "Opening quote card", "stock line used"))
    else:
        items.append((True, "Opening quote card", _quote_text(script["intro_epigraph"])[:60]))
    if len(chapters) >= 3:
        if not _real(_quote_text(script.get("verdict_epigraph"))):
            script["verdict_epigraph"] = stock_epigraph(
                topic, verdict=True, exclude_texts={_quote_text(script["intro_epigraph"])})
            items.append(("fixed", "Second quote card", "stock line used"))
        else:
            items.append((True, "Second quote card", _quote_text(script["verdict_epigraph"])[:60]))

    all_scenes = [s for c in chapters for s in c.get("scenes") or []]
    plan = (story or {}).get("plan") or {}
    maps = sum(1 for s in all_scenes if s.get("map"))
    places = [p for p in plan.get("places") or [] if p]
    if places and not maps:
        items.append((False, "Maps", f"none placed although the story has {', '.join(places[:4])}"))
    else:
        items.append((True, "Maps", f"{maps}"))

    cards = sum(1 for s in all_scenes if s.get("dossier"))
    if plan.get("cast") and not cards:
        items.append((False, "Cast cards", "none placed"))
    else:
        items.append((True, "Cast cards", f"{cards}"))

    if story and story.get("sources"):
        if not (script.get("source_article") or {}).get("url") and story["sources"].get("url"):
            script["source_article"] = {"title": story["sources"].get("title"),
                                        "url": story["sources"]["url"]}
            items.append(("fixed", "Sources", "article link restored"))
        n = sum(1 for s in scenes or () if s.get("claim_source"))
        works = len(script.get("claim_sources") or [])
        items.append((True if n else False, "Sources on screen",
                      f"{n} scenes, {works} works cited" if n else "no scene carries a source"))

    words = script.get("word_count") or sum(len((s.get("narration") or "").split())
                                            for s in all_scenes)
    if words < 400:
        items.append(("block", "Length", f"{words} words — not a video"))
    return items


def upload_check(title, description, thumb_path, variants=()):
    items = []
    if not _real(title):
        items.append(("block", "Final title", repr(title)))
    bad = [ln for ln in (description or "").splitlines() if ln.strip() and not _real(ln)
           and not re.search(r"\d", ln)]
    if bad:
        items.append(("block", "Final description", f"placeholder line {bad[0]!r}"))
    if not thumb_path or not os.path.exists(thumb_path):
        items.append((False, "Thumbnail", "none — YouTube picks a frame"))
    else:
        n = len(variants or ())
        items.append((True if n >= 3 else False, "Thumbnails", f"{n} of 3 for the rotation"))
    return items


def report(items, stage):
    """Prints the items, adds them to the job summary, and stops the run if
    any is blocking."""
    mark = {True: "✅", "fixed": "🔧", False: "⚠️", "block": "❌"}
    print(f"📋 Checklist ({stage}):")
    for ok, name, note in items:
        print(f"   {mark[ok]} {name}: {note}")
    path = os.getenv("GITHUB_STEP_SUMMARY")
    if path:
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"\n### Checklist — {stage}\n\n| | Item | Note |\n|---|---|---|\n")
                for ok, name, note in items:
                    f.write(f"| {mark[ok]} | {name} | {str(note).replace('|', '/')} |\n")
        except Exception:
            pass
    blocked = [n for ok, n, _ in items if ok == "block"]
    if blocked:
        raise SystemExit(f"❌ Checklist stopped the run: {', '.join(blocked)}.")
