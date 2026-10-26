"""
The THEN / NOW opening on its own, for review before it goes on a video
(owner, 26 Oct: "work only on the opening, generate it, and if we like it
glue it to the video"). Run by .github/workflows/opening_preview.yml.

    python tools/opening_preview.py out

Settings come from the environment:
  NOW_QUERIES / THEN_QUERIES   searches, separated by |
  NOW_WIKI / THEN_WIKI         Wikipedia articles whose pictures (and Commons
                               category) are candidates too, separated by |
  NOW_PICK / THEN_PICK         words from the chosen picture's title (safest),
                               or its number on this run's contact sheet
  THEN_YEAR, THEN_DATE         the year the counter lands on, the camcorder date
  NOW_LINE / THEN_LINE         the paradox, one line under each side
  OWNER_MEDIA_DIR              now.* / then.* there win over any search

Writes out/now_sheet.jpg and out/then_sheet.jpg (numbered candidates),
out/candidates.json and out/opening.mp4 (with the counter's ticks).
"""

import json
import os
import subprocess
import sys

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import archive_engine as ae  # noqa: E402
import media_sources as ms  # noqa: E402
import native_short as ns  # noqa: E402
import owner_media  # noqa: E402

TILE_W, TILE_H, COLS = 360, 203, 5


def _split(name):
    return [q.strip() for q in os.getenv(name, "").split("|") if q.strip()]


def candidates(queries, wikis):
    """Photos and footage for one side: each article's pictures and its
    Commons category, then Commons, DVIDS and Openverse searches."""
    out, seen = [], set()

    def add(cands, kind):
        for c in cands or []:
            if c and c.get("url") and c["url"] not in seen \
                    and not ms.OFF_TOPIC.search(c.get("title") or ""):
                seen.add(c["url"])
                out.append({**c, "kind": kind})
    for title in wikis:
        add([ae.article_lead_image(title)], "photo")
        add(ae.article_images(title), "photo")
        cat = ae.commons_category_of(title)
        if cat:
            add(ae.category_images(cat), "photo")
    for q in queries:
        add(ae.search_commons_video(q, limit=10), "video")
        add(ms.search_dvids(q, "video", 8), "video")
        add(ae.search_commons(q, limit=12), "photo")
        add(ms.search_dvids(q, "image", 8), "photo")
        add(ms.search_openverse(q, 8), "photo")
    return out


def _thumb(c, work):
    """A still of the candidate: the photo, or a frame 3 s into the clip."""
    try:
        if c["kind"] == "video":
            path = os.path.join(work, "frame.jpg")
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "3", "-i", c["url"],
                            "-frames:v", "1", path], timeout=60, check=True)
            return Image.open(path).convert("RGB")
        path = os.path.join(work, "photo.jpg")
        if ae.download_archival_image(c, path, TILE_W * 2, TILE_H * 2):
            return Image.open(path).convert("RGB")
    except Exception:
        pass
    return None


def sheet(cands, out_path, work):
    rows = max(1, (len(cands) + COLS - 1) // COLS)
    img = Image.new("RGB", (COLS * TILE_W, rows * (TILE_H + 46)), (16, 16, 16))
    d = ImageDraw.Draw(img)
    for i, c in enumerate(cands):
        x, y = (i % COLS) * TILE_W, (i // COLS) * (TILE_H + 46)
        t = _thumb(c, work)
        if t:
            t.thumbnail((TILE_W - 6, TILE_H))
            img.paste(t, (x + (TILE_W - t.width) // 2, y))
        d.rectangle((x, y, x + 46, y + 26), fill=(255, 150, 40))
        d.text((x + 6, y + 6), f"{i}", fill=(0, 0, 0))
        d.text((x + 6, y + TILE_H + 4),
               f"{'VIDEO ' if c['kind'] == 'video' else ''}{c.get('year') or '?'}",
               fill=(255, 200, 120))
        d.text((x + 6, y + TILE_H + 22), (c.get("title") or "")[:54], fill=(235, 235, 235))
    img.save(out_path, quality=85)


def fetch(c, path_base, seconds):
    """The chosen candidate as a local file: a clip of `seconds`, or the photo."""
    if c["kind"] == "video":
        path = path_base + ".mp4"
        if ae.fetch_commons_video(c, path, seconds=seconds + 1.0):
            return path
        return None
    path = path_base + ".jpg"
    return path if ae.download_archival_image(c, path, 1920, 1080) else None


def main(out):
    os.makedirs(out, exist_ok=True)
    work = os.path.join(out, "work")
    os.makedirs(work, exist_ok=True)
    seconds = ns.WIDE_SECONDS
    sides, report = {}, {}
    for side in ("now", "then"):
        mine = owner_media.opening(side)
        cands = candidates(_split(f"{side.upper()}_QUERIES"), _split(f"{side.upper()}_WIKI"))
        report[side] = [{"n": i, "kind": c["kind"], "title": c.get("title"), "year": c.get("year"),
                         "url": c.get("url"), "credit": ae.format_credit(c)}
                        for i, c in enumerate(cands)]
        print(f"{side.upper()}: {len(cands)} candidate(s)")
        if cands:
            sheet(cands[:40], os.path.join(out, f"{side}_sheet.jpg"), work)
        if mine:
            sides[side] = mine[0]
            continue
        # a number from the sheet, or words from the picture's title: search
        # results come back in another order on the next run (26 Oct: #22
        # was Dimona on the sheet, a glider on the render)
        want = os.getenv(f"{side.upper()}_PICK", "0").strip() or "0"
        if want.isdigit():
            pick = int(want)
        else:
            pick = next((i for i, c in enumerate(cands)
                         if want.lower() in (c.get("title") or "").lower()), None)
            if pick is None:
                raise SystemExit(f"❌ No {side} candidate titled like {want!r}")
        order = [pick] + [i for i in range(len(cands)) if i != pick]
        for i in order:
            if i < len(cands):
                got = fetch(cands[i], os.path.join(work, side), seconds)
                if got:
                    sides[side] = got
                    print(f"{side.upper()}: #{i} {cands[i].get('title', '')[:70]}")
                    break
    with open(os.path.join(out, "candidates.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    if "now" not in sides or "then" not in sides:
        raise SystemExit(f"❌ Nothing usable for: {[s for s in ('now', 'then') if s not in sides]}")

    year = int(os.getenv("THEN_YEAR") or 0) or None
    lines = (os.getenv("NOW_LINE", "").strip(), os.getenv("THEN_LINE", "").strip())
    silent = os.path.join(work, "opening_silent.mp4")
    ticks = ns.intro_clip_wide(sides["now"], sides["then"], seconds, {}, work, silent,
                               then_year=year, then_date=os.getenv("THEN_DATE") or None,
                               captions=lines if any(lines) else None)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", silent, "-i", ticks,
                    "-filter_complex", f"[1:a]apad,atrim=0:{seconds}[a]",
                    "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac",
                    os.path.join(out, "opening.mp4")], check=True)
    print(f"✅ Opening: {os.path.join(out, 'opening.mp4')} ({seconds:.1f}s)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "out")
