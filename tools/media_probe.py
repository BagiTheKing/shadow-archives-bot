"""
What DVIDS and Openverse return for a subject: a contact sheet per source
(the first results' pictures with their titles and years) and the raw API
replies, for a look before they feed real videos. Run by
.github/workflows/media_probe.yml.

    python tools/media_probe.py out_dir "query one" "query two" ...
"""

import io
import json
import os
import sys

import requests
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import media_sources as ms  # noqa: E402

TILE_W, TILE_H, COLS = 320, 220, 4


def _tile(cand):
    url = cand.get("url")
    tile = Image.new("RGB", (TILE_W, TILE_H + 44), (20, 20, 20))
    try:
        if cand.get("duration") is not None:
            raise ValueError("video")
        r = requests.get(url, headers=ms.HEADERS, timeout=30)
        img = Image.open(io.BytesIO(r.content)).convert("RGB")
        img.thumbnail((TILE_W, TILE_H))
        tile.paste(img, ((TILE_W - img.width) // 2, (TILE_H - img.height) // 2))
    except Exception:
        ImageDraw.Draw(tile).text((10, TILE_H // 2), "VIDEO" if cand.get("duration")
                                  is not None else "no preview", fill=(200, 200, 200))
    d = ImageDraw.Draw(tile)
    d.text((6, TILE_H + 4), (cand.get("title") or "")[:52], fill=(255, 255, 255))
    d.text((6, TILE_H + 22), f"{cand.get('year') or '?'}  {cand.get('width')}x"
           f"{cand.get('height')}  {cand.get('duration') or ''}"[:52], fill=(230, 180, 60))
    return tile


def sheet(cands, path):
    if not cands:
        return
    rows = (len(cands) + COLS - 1) // COLS
    out = Image.new("RGB", (TILE_W * COLS, (TILE_H + 44) * rows), (0, 0, 0))
    for i, c in enumerate(cands):
        out.paste(_tile(c), ((i % COLS) * TILE_W, (i // COLS) * (TILE_H + 44)))
    out.save(path, quality=85)


def main(out, *queries):
    os.makedirs(out, exist_ok=True)
    report, raw = {}, []
    for q in queries:
        slug = "".join(ch if ch.isalnum() else "_" for ch in q.lower())[:40]
        from archive_engine import search_commons, search_loc
        found = {
            "commons": search_commons(q, limit=8),
            "loc": search_loc(q),
            "dvids_photo": ms.search_dvids(q, "image", 8, raw=raw),
            "dvids_video": ms.search_dvids(q, "video", 6, raw=raw),
            "commons_video": __import__("archive_engine").search_commons_video(q)[:6],
            "openverse": ms.search_openverse(q, 8, raw=raw),
        }
        report[q] = {k: [{x: c.get(x) for x in ("title", "year", "width", "height",
                                                  "duration", "url", "page", "license",
                                                  "source")}
                         for c in v] for k, v in found.items()}
        for k, v in found.items():
            print(f"{q!r} {k}: {len(v)}")
            sheet(v, os.path.join(out, f"{slug}__{k}.jpg"))
    json.dump(report, open(os.path.join(out, "report.json"), "w"), indent=1)
    json.dump(raw[:12], open(os.path.join(out, "raw.json"), "w"), indent=1, default=str)


if __name__ == "__main__":
    main(sys.argv[1], *sys.argv[2:])
