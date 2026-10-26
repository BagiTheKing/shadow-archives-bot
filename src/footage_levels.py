"""
Washed-out archive footage, measured and repaired at the moment it is cut,
for every pipeline (the long video, the native Short, the clips).

The Cuba Short's 1962 newsreel had no blacks at all: its darkest 5% sat at
~110 of 255, where good footage sits under 35, and it read as a grey wash
exactly where the viewers left (0:03-0:08, 25 Oct). A clip's levels are
taken from three frames; a washed-out or murky one is stretched back to
real blacks and whites, and its levels before the repair are kept on the
candidate (cand["_levels"]) so a caller with a choice can still prefer a
clip that was good to begin with.
"""

import os
import subprocess

PALE = 70        # darkest 5% above this: a grey wash
DARK = 60        # brightest 5% below this: murk, nothing to see
FLAT_BLACK, FLAT_WHITE = 40, 150   # repaired beyond these; good footage untouched


def levels(path):
    """(black, white): the median over three frames of the 5th and 95th
    luma percentiles, 0-255."""
    import numpy as np
    from PIL import Image
    lo, hi = [], []
    for at in ("0.4", "1.5", "2.6"):
        png = path + ".lv.png"
        try:
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", at, "-i", path,
                            "-frames:v", "1", "-vf", "scale=270:-2", png], check=True)
            a = np.asarray(Image.open(png).convert("L"), float)
            p5, p95 = np.percentile(a, [5, 95])
            lo.append(p5)
            hi.append(p95)
        except Exception:
            continue
        finally:
            if os.path.exists(png):
                os.remove(png)
    if not lo:
        return 0.0, 255.0
    return sorted(lo)[len(lo) // 2], sorted(hi)[len(hi) // 2]


def restore(path, lv):
    """Stretches a flat clip back to real blacks and whites, in place."""
    lo, hi = lv
    if lo <= FLAT_BLACK and hi >= FLAT_WHITE:
        return
    lo, hi = min(lo, 160) / 255, max(hi, lo + 60) / 255
    tmp = path + ".lv.mp4"
    r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", path, "-vf",
                        f"colorlevels=rimin={lo:.3f}:gimin={lo:.3f}:bimin={lo:.3f}:"
                        f"rimax={hi:.3f}:gimax={hi:.3f}:bimax={hi:.3f}",
                        "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
                        "-pix_fmt", "yuv420p", tmp])
    if r.returncode == 0:
        os.replace(tmp, path)
    elif os.path.exists(tmp):
        os.remove(tmp)


def fix(path):
    """Measures a freshly cut clip and repairs it; returns its levels as
    they were. Never raises."""
    try:
        lv = levels(path)
        if lv[0] > FLAT_BLACK or lv[1] < FLAT_WHITE:
            restore(path, lv)
            if lv[0] > PALE:
                print(f"   🎚️  Washed-out footage restored (black was {lv[0]:.0f}).")
        return lv
    except Exception as e:
        print(f"   ↩️  Footage levels not checked ({type(e).__name__}: {e}).")
        return 0.0, 255.0
