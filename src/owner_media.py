"""
Pictures the owner made or chose for one video (owner, 26 Oct: "give me the
prompts for the images and I will generate them"). OWNER_MEDIA_DIR names a
folder in the repository, e.g. owner_media/israel-nuclear/, holding any of:

- now.jpg / now.png / now.mp4    today's side of the THEN / NOW opening
- then.jpg / then.png / then.mp4 the past's side
- scene_012.jpg (or .png)        scene 12's picture, in place of a generated
                                 one; scene_012_1.jpg is its second shot

Scene numbers are the ones a run prints ("Scene 12: generating shot 1/1")
and writes to build/image_prompts.json, so a re-run of the same saved
script lines up. A missing folder or file changes nothing.
"""

import glob
import os

IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp")
CREDIT = "Illustration made for this video"


def _dir():
    d = os.getenv("OWNER_MEDIA_DIR", "").strip()
    return d if d and os.path.isdir(d) else None


def _first(stem, exts):
    d = _dir()
    if not d:
        return None
    for ext in exts:
        for p in glob.glob(os.path.join(d, stem + ext)) + glob.glob(os.path.join(d, stem + ext.upper())):
            return p
    return None


def opening(side):
    """(path, credit) for "now" or "then", or None."""
    p = _first(side, (".mp4",) + IMAGE_EXT)
    if p:
        print(f"🎨 Owner's {side.upper()} for the opening: {os.path.basename(p)}")
        return p, CREDIT
    return None


def scene_images(idx):
    """The owner's pictures for scene idx, shot order, or []."""
    out = []
    first = _first(f"scene_{idx:03d}", IMAGE_EXT)
    if first:
        out.append(first)
    for shot in range(1, 4):
        p = _first(f"scene_{idx:03d}_{shot}", IMAGE_EXT)
        if p:
            out.append(p)
    return out
