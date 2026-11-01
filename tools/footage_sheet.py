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
        # exact clips (links separated by spaces), downloaded once and decoded
        # in one pass at a frame every 8 s: seeking a WebM over HTTP failed,
        # and Commons' transcodes report no duration
        import glob
        import requests
        index = []
        for n, link in enumerate(query.split()):
            src = os.path.join(out, f"src_{n}.webm")
            with requests.get(link, headers=HEADERS, stream=True, timeout=300) as resp:
                resp.raise_for_status()
                with open(src, "wb") as f:
                    for chunk in resp.iter_content(1 << 20):
                        f.write(chunk)
            fdir = os.path.join(out, f"tmp_frames_{n}")
            os.makedirs(fdir, exist_ok=True)
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-vf",
                            "fps=1/8,scale=320:-2", os.path.join(fdir, "f_%04d.jpg")], check=True)
            frames = sorted(glob.glob(os.path.join(fdir, "f_*.jpg")))
            keep = frames if len(frames) <= 42 else frames[::max(1, len(frames) // 42)]
            tiles = []
            for fpath in keep:
                t = (frames.index(fpath)) * 8
                im = Image.open(fpath).convert("RGB")
                ImageDraw.Draw(im).text((6, 6), f"{t}s", fill=(255, 220, 0))
                tiles.append(im)
            if tiles:
                w, h = tiles[0].size
                sheet = Image.new("RGB", (w * 6, h * ((len(tiles) + 5) // 6)))
                for i, im in enumerate(tiles):
                    sheet.paste(im, ((i % 6) * w, (i // 6) * h))
                sheet.save(os.path.join(out, f"clip_{n}.jpg"), quality=85)
            index.append({"k": n, "url": link, "frames": len(frames), "seconds": len(frames) * 8})
            print(n, link.rsplit("/", 1)[-1][:60], len(frames) * 8, "s")
            import shutil
            shutil.rmtree(fdir)
            os.remove(src)
        json.dump(index, open(os.path.join(out, "clips.json"), "w"), indent=1)
        return
    if True:
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
        index.append({"k": k, "title": c.get("title"), "url": c.get("_link") or c["url"],
                      "duration": dur, "year": c.get("year")})
        print(k, c.get("title"), dur)
    json.dump(index, open(os.path.join(out, "clips.json"), "w"), indent=1)
    for f in os.listdir(out):                  # the downloads stay off the branch
        if f.startswith(("src_", "tmp_")):
            os.remove(os.path.join(out, f))


if __name__ == "__main__":
    main(*sys.argv[1:4])
