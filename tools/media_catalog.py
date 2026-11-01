"""
Everything free that exists for one story, numbered, so its pictures can be
chosen by eye and pinned to scenes (owner, 1 Nov: "videos are always
favoured"; the failure-point episodes list an event's whole Commons
categories and the Internet Archive before choosing). Run by
.github/workflows/media_catalog.yml.

    python tools/media_catalog.py out_dir

Environment: CATEGORIES (Commons categories, separated by |; their direct
subcategories are included), QUERIES (Commons and Internet Archive
searches, separated by |), MAX_YEAR (drop anything dated later).

Writes out_dir/catalog.json (V1.. footage, P1.. photos, each with its file,
playable url, year, duration, licence and author) and contact sheets
out_dir/videos_N.jpg and out_dir/photos_N.jpg.
"""

import io
import json
import os
import re
import sys

import requests
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import archive_engine as ae  # noqa: E402

TW, TH, COLS, ROWS = 360, 203, 5, 8
JUNK = re.compile(r"\.svg$|\bflag\b|\blogo\b|\bseal of\b|coat of arms|\bribbon\b|insignia|"
                  r"\bmedal\b|\bemblem\b|\bsignature\b|\bicon\b", re.I)


def _year(text):
    m = re.findall(r"\b(19\d\d|20[0-3]\d)\b", text or "")
    return int(m[0]) if m else None


def _entry(page):
    info = (page.get("videoinfo") or page.get("imageinfo") or [{}])[0]
    if not info.get("url"):
        return None
    title = page.get("title", "").replace("File:", "")
    if JUNK.search(title):
        return None
    meta = info.get("extmetadata") or {}
    lic = ae._strip_html((meta.get("LicenseShortName") or {}).get("value", ""))
    if "fair use" in lic.lower() or "non-free" in lic.lower():
        return None
    mime = info.get("mime", "")
    video = mime.startswith("video") or title.lower().endswith((".webm", ".ogv", ".mp4", ".mpg"))
    if not video and not mime.startswith("image"):
        return None
    date = ae._strip_html((meta.get("DateTimeOriginal") or {}).get("value", ""))
    desc = ae._strip_html((meta.get("ImageDescription") or {}).get("value", ""))
    return {"kind": "video" if video else "photo", "title": title, "year": _year(date) or _year(title),
            "duration": float(info.get("duration") or 0) or None,
            "url": info["url"] if not info["url"].startswith("//") else "https:" + info["url"],
            "play": ae._best_derivative(info) if video else None,
            "thumb": info.get("thumburl") or info.get("url"),
            "license": lic, "artist": ae._strip_html((meta.get("Artist") or {}).get("value", ""))[:80],
            "about": desc[:300], "source": "Wikimedia Commons"}


def _query(params):
    base = {"action": "query", "format": "json", "prop": "videoinfo",
            "viprop": "url|size|mime|extmetadata|derivatives", "viurlwidth": 480}
    r = ae._polite_get(ae.COMMONS_API, params={**base, **params}, timeout=40)
    r.raise_for_status()
    return (r.json().get("query") or {}).get("pages") or {}


def category(cat, depth=1, seen=None):
    seen = seen if seen is not None else set()
    name = cat if cat.startswith("Category:") else f"Category:{cat}"
    if name in seen:
        return []
    seen.add(name)
    out, subs = [], []
    try:
        pages = _query({"generator": "categorymembers", "gcmtitle": name,
                        "gcmtype": "file|subcat", "gcmlimit": 500})
    except Exception as e:
        print(f"⚠️ {name}: {e}")
        return []
    for p in pages.values():
        if p.get("ns") == 14:
            subs.append(p["title"])
        else:
            e = _entry(p)
            if e:
                out.append(e)
    print(f"📁 {name}: {len(out)} file(s), {len(subs)} subcategories")
    for s in subs[:12] if depth > 0 else []:
        out += category(s, depth - 1, seen)
    return out


def search(q, kind):
    try:
        pages = _query({"generator": "search", "gsrnamespace": 6, "gsrlimit": 50,
                        "gsrsearch": f"filetype:{kind} {q}"})
    except Exception as e:
        print(f"⚠️ search {q!r}: {e}")
        return []
    return [e for e in (_entry(p) for p in pages.values()) if e]


def archive_org(q):
    out = []
    for d in ae.search_archival_video(q, limit=15):
        out.append({"kind": "video", "title": d["title"], "year": _year(d["title"]),
                    "duration": None, "url": f"https://archive.org/details/{d['identifier']}",
                    "play": None, "identifier": d["identifier"],
                    "thumb": f"https://archive.org/services/img/{d['identifier']}",
                    "license": "Internet Archive (trusted collection)", "artist": "",
                    "about": "", "source": "Internet Archive"})
    return out


def sheets(items, prefix, out):
    per = COLS * ROWS
    for page in range(0, len(items), per):
        chunk = items[page:page + per]
        rows = (len(chunk) + COLS - 1) // COLS
        img = Image.new("RGB", (COLS * TW, rows * (TH + 46)), (16, 16, 16))
        d = ImageDraw.Draw(img)
        for i, c in enumerate(chunk):
            x, y = (i % COLS) * TW, (i // COLS) * (TH + 46)
            try:
                r = ae._polite_get(c["thumb"], timeout=30)
                t = Image.open(io.BytesIO(r.content)).convert("RGB")
                t.thumbnail((TW - 6, TH))
                img.paste(t, (x + (TW - t.width) // 2, y))
            except Exception:
                d.text((x + 10, y + TH // 2), "no preview", fill=(200, 200, 200))
            d.rectangle((x, y, x + 64, y + 26), fill=(255, 150, 40))
            d.text((x + 6, y + 6), c["id"], fill=(0, 0, 0))
            dur = f" {int(c['duration'])}s" if c.get("duration") else ""
            d.text((x + 6, y + TH + 4), f"{c.get('year') or '?'}{dur}  {c['license'][:22]}",
                   fill=(255, 200, 120))
            d.text((x + 6, y + TH + 22), c["title"][:54], fill=(235, 235, 235))
        img.save(os.path.join(out, f"{prefix}_{page // per + 1}.jpg"), quality=82)


def main(out):
    os.makedirs(out, exist_ok=True)
    cats = [c.strip() for c in os.getenv("CATEGORIES", "").split("|") if c.strip()]
    queries = [q.strip() for q in os.getenv("QUERIES", "").split("|") if q.strip()]
    max_year = int(os.getenv("MAX_YEAR") or 0) or None
    found, seen_cats = [], set()
    for c in cats:
        found += category(c, seen=seen_cats)
    for q in queries:
        found += search(q, "video") + search(q, "bitmap") + archive_org(q)
    items, urls = [], set()
    for c in found:
        if c["url"] in urls or (max_year and c.get("year") and c["year"] > max_year):
            continue
        urls.add(c["url"])
        items.append(c)
    videos = [c for c in items if c["kind"] == "video"]
    photos = [c for c in items if c["kind"] == "photo"]
    for n, c in enumerate(videos, 1):
        c["id"] = f"V{n}"
    for n, c in enumerate(photos, 1):
        c["id"] = f"P{n}"
    with open(os.path.join(out, "catalog.json"), "w", encoding="utf-8") as f:
        json.dump(videos + photos, f, ensure_ascii=False, indent=1)
    sheets(videos, "videos", out)
    sheets(photos, "photos", out)
    print(f"✅ {len(videos)} video(s), {len(photos)} photo(s)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "out")
