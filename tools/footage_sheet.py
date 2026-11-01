"""
Contact sheets of the real footage a search finds: one frame every few
seconds of each clip, stamped with its time, to pick the exact moment a
Short should use (script "now_start" / "now2_start"). Run by
.github/workflows/media_probe.yml with mode=footage.

    python tools/footage_sheet.py out_dir "query" [recent_year]
"""

import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def main(out, query, recent=None):
    from PIL import Image, ImageDraw
    from archive_engine import HEADERS
    from native_short import _video_candidates
    os.makedirs(out, exist_ok=True)
    if query.startswith("http"):
        # exact clips (links separated by spaces): a frame every 8 s, up to 40
        cands = []
        for u in query.split():
            r = subprocess.run(["ffprobe", "-v", "error", "-user_agent", HEADERS["User-Agent"],
                                "-show_entries", "format=duration", "-of", "csv=p=0", u],
                               capture_output=True, text=True, timeout=90)
            dur = float((r.stdout or "0").strip() or 0)
            cands.append({"url": u, "title": u.rsplit("/", 1)[-1][:80], "duration": dur,
                          "_step": 8.0, "_max": 40})
    else:
        cands = _video_candidates([query], recent=int(recent) if recent else None)[:5]
    index = []
    for k, c in enumerate(cands):
        dur = float(c.get("duration") or 0)
        step = c.get("_step") or max(3.0, dur / 24)
        tiles, t = [], 1.0
        while t < dur and len(tiles) < (c.get("_max") or 24):
            f = os.path.join(out, f"tmp_{k}.jpg")
            r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-user_agent",
                                HEADERS["User-Agent"], "-ss", f"{t:.1f}", "-i", c["url"],
                                "-frames:v", "1", "-vf", "scale=320:-2", f],
                               capture_output=True, timeout=90)
            if r.returncode == 0 and os.path.exists(f):
                im = Image.open(f).convert("RGB")
                ImageDraw.Draw(im).text((6, 6), f"{t:.0f}s", fill=(255, 220, 0))
                tiles.append(im)
            t += step
        if not tiles:
            continue
        w, h = tiles[0].size
        sheet = Image.new("RGB", (w * 6, h * ((len(tiles) + 5) // 6)))
        for i, im in enumerate(tiles):
            sheet.paste(im.resize((w, h)), ((i % 6) * w, (i // 6) * h))
        sheet.save(os.path.join(out, f"clip_{k}.jpg"), quality=85)
        index.append({"k": k, "title": c.get("title"), "url": c["url"],
                      "duration": dur, "year": c.get("year")})
        print(k, c.get("title"), dur)
    json.dump(index, open(os.path.join(out, "clips.json"), "w"), indent=1)


if __name__ == "__main__":
    main(*sys.argv[1:4])
