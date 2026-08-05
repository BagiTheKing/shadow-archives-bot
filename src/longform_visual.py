"""
Long-form visual engine — 16:9, archival-first.

The old version of this file produced the "bad PowerPoint" problem: one AI
image per ~45 words meant a frame could sit on screen for 30+ seconds, chapter
openers were flat locally-drawn title cards, and every single visual was an AI
painting. Three changes fix that:

1. REAL IMAGERY FIRST. Each scene carries an archive_query naming its real,
   photographable subject. Wikimedia Commons is searched before anything is
   generated; a genuine photograph of the ship, the treaty or the man beats an
   AI guess every time one exists. Optional muted Pexels b-roll fills
   atmospheric scenes when a PEXELS_API_KEY is configured. Pollinations remains
   the fallback, not the default.

2. NO SHOT OUTLIVES ITS WELCOME. A scene longer than MAX_SHOT_SECONDS gets
   extra images (a second Commons result, or a re-seeded generation), and the
   video engine cuts between them inside the scene. The cutting rhythm no
   longer depends on how the LLM happened to segment the narration.

3. CHAPTER CARDS ARE GONE. Chapter-first scenes get real imagery like any
   other scene; the chapter title is typeset over the footage by the video
   engine, the way an actual documentary does it, instead of pausing the film
   for a slide.

Frames are still saved pre-upscaled at KB_W x KB_H so the renderer needs no
per-frame scale filter (see longform_video.py for why that matters).
"""

import math
import os
import re
import time

from PIL import Image

from archive_engine import (search_archives, search_commons,
                            download_archival_image, format_credit,
                            pexels_available, fetch_broll_video,
                            extract_poster_frame)
from visual_engine import download_image, make_fallback_image

# Delivery resolution for the long-form video.
LF_W, LF_H = 1920, 1080

# 1.5x delivery: enough headroom for a 1.2x Ken Burns zoom to stay above 1080p,
# without the 4x-area cost of the Shorts path.
KB_W, KB_H = int(LF_W * 1.5), int(LF_H * 1.5)

THROTTLE_SECONDS = 6            # Pollinations anonymous rate limit

# Longest a single still may hold the screen. Thirteen seconds is already slow
# by documentary standards; past it the video reads as a slideshow.
MAX_SHOT_SECONDS = 13.0
MAX_SHOTS_PER_SCENE = 3

# B-roll is connective tissue, not the main dish: too many stock clips and the
# video stops looking like it is about anything in particular.
MAX_BROLL_CLIPS = 6
MIN_BROLL_SCENE_SECONDS = 6.0

# Minimum scenes between two b-roll clips. The first live run granted b-roll
# first-come-first-served, so every clip landed in the opening minutes and the
# back half was wall-to-wall stills. Spacing them out is what "mixed" means.
BROLL_MIN_GAP = 4

# Photography exists from roughly here; before it, a Commons "photo" of the
# subject is by definition a modern anachronism.
PHOTOGRAPHY_YEAR = 1840

# Titles containing these read as period-appropriate artwork even when the
# file also carries a modern year (a 1907 painting OF 1415 is fine content).
ART_WORDS = ("painting", "engraving", "portrait", "tapestry", "manuscript",
             "miniature", "fresco", "woodcut", "etching", "drawing",
             "illustration", "map", "chart", "azulejo", "mosaic", "relief")

# Modern-photo tells in a Commons title. Only consulted for pre-photography
# stories, where a match means the search drifted to the present day —
# the "2020s aerial of Ceuta over 1415 narration" failure.
_MODERN_TITLE_RE = re.compile(r"\b(19|20)\d{2}\b")
_MODERN_TITLE_WORDS = ("aerial", "panorama", "skyline", "beach", "street",
                       "marina", "airport", "stadium", "modern", "night view")


def _era_appropriate(candidate: dict, era_year) -> bool:
    """Whether a Commons candidate can appear in a story set in era_year."""
    if not era_year or era_year >= PHOTOGRAPHY_YEAR:
        return True
    low = candidate.get("title", "").lower()
    if any(a in low for a in ART_WORDS):
        return True
    if _MODERN_TITLE_RE.search(low):
        return False
    return not any(w in low for w in _MODERN_TITLE_WORDS)


def _shots_for(duration: float) -> int:
    """How many distinct images a scene of this length needs."""
    if not duration or duration <= 0:
        return 1
    return min(MAX_SHOTS_PER_SCENE, max(1, math.ceil(duration / MAX_SHOT_SECONDS)))


def _to_kb_size(path: str):
    """Upscale a downloaded frame to the Ken Burns working size, once."""
    img = Image.open(path).convert("RGB")
    if (img.width, img.height) != (KB_W, KB_H):
        img = img.resize((KB_W, KB_H), Image.LANCZOS)
    img.save(path, "JPEG", quality=92)


def _reuse_real_frame(pool, output_path: str, variant: int) -> bool:
    """Re-frames an already-downloaded archival image as a stand-in for a
    failed generation. Punched in and offset so it reads as a different shot
    of the same material rather than a repeat.

    Not mirrored on purpose: flipping an archival photograph reverses insignia,
    signage and text, which on a history channel is worse than reusing it.
    """
    if not pool:
        return False
    source = pool[variant % len(pool)]
    try:
        img = Image.open(source).convert("RGB")
    except Exception:
        return False

    # 78% centre-ish crop, nudged by the variant so successive reuses of the
    # same source frame different parts of it.
    scale = 0.78
    cw, ch = int(img.width * scale), int(img.height * scale)
    max_dx, max_dy = img.width - cw, img.height - ch
    dx = int(max_dx * (0.25 + 0.5 * ((variant % 3) / 2.0)))
    dy = int(max_dy * (0.25 + 0.5 * ((variant % 2) / 1.0)))
    img = img.crop((dx, dy, dx + cw, dy + ch)).resize((KB_W, KB_H), Image.LANCZOS)
    img.save(output_path, "JPEG", quality=92)
    return True


def _vary_prompt(prompt: str, shot: int) -> str:
    """A second AI shot of the same segment should not be the same picture.
    Re-angling the prompt (plus Pollinations' per-call random seed) gives the
    editor a genuinely different frame to cut to."""
    angles = ["", "extreme close-up detail of the scene, ",
              "wide establishing shot of the scene, ", "low-angle view, "]
    return angles[shot % len(angles)] + prompt


def generate_longform_visuals(scenes: list, output_dir: str = "build") -> list:
    """Fills each scene with image_paths (1-3 frames), possibly a video_path
    (muted b-roll), and a credit string when a real archival photo was used."""
    print(f"🎨 Long-form visual engine ({LF_W}x{LF_H}, working at {KB_W}x{KB_H})...")
    os.makedirs(output_dir, exist_ok=True)

    api_calls, archival_used, broll_used = 0, 0, 0
    last_broll_idx = -BROLL_MIN_GAP - 1
    # Nothing real may appear twice: one live run used the same C-130
    # photograph for two scenes and the same stock clip four times, which is
    # what "repetitive" looked like on screen.
    used_urls, used_broll_queries = set(), set()
    # Every real frame obtained so far. When generation fails, a re-framed
    # archival image stands in — infinitely better than the black screen the
    # designed fallback produces.
    real_pool = []

    for idx, scene in enumerate(scenes):
        wanted = _shots_for(scene.get("duration", 0))
        era = scene.get("era_year")
        images, credits = [], []

        # ---- 1) Real archival imagery from Wikimedia Commons ----
        # For post-photography stories: genuine photographs. For older ones:
        # period paintings, engravings and maps — filtered so a modern photo
        # of the place can never stand in for its fifteenth-century self.
        query = (scene.get("archive_query") or "").strip()
        if query:
            candidates = [c for c in search_archives(query)
                          if _era_appropriate(c, era)
                          and c.get("url") not in used_urls]
            if (not candidates and era and era < PHOTOGRAPHY_YEAR
                    and not any(a in query.lower() for a in ART_WORDS)):
                candidates = [c for c in search_commons(f"{query} painting")
                              if _era_appropriate(c, era)
                              and c.get("url") not in used_urls]
            for cand in candidates:
                if len(images) >= wanted:
                    break
                path = os.path.join(output_dir,
                                    f"lf_scene_{idx:03d}_{len(images)}.jpg")
                if download_archival_image(cand, path, KB_W, KB_H):
                    images.append(path)
                    credits.append(format_credit(cand))
                    used_urls.add(cand.get("url"))
            if images:
                archival_used += len(images)
                scene["is_real"] = True
                real_pool.extend(images)
                print(f"📜 Scene {idx}: {len(images)} archival image(s) "
                      f"for {query!r}")

        # ---- 2) Muted stock b-roll, timeless subjects only (optional) ----
        # Spaced through the video rather than granted first-come, so clips
        # and stills stay interleaved instead of clustering at the front.
        if (not images and pexels_available() and broll_used < MAX_BROLL_CLIPS
                and idx - last_broll_idx > BROLL_MIN_GAP
                and scene.get("duration", 0) >= MIN_BROLL_SCENE_SECONDS):
            broll_query = _timeless_broll_query(scene, used_broll_queries)
            video_path = os.path.join(output_dir, f"lf_broll_{idx:03d}.mp4")
            if broll_query and fetch_broll_video(broll_query, video_path,
                                                 min_duration=4.0):
                poster = os.path.join(output_dir, f"lf_scene_{idx:03d}_0.jpg")
                if extract_poster_frame(video_path, poster, KB_W, KB_H):
                    scene["video_path"] = video_path
                    scene["image_path"] = poster
                    scene["image_paths"] = [poster]
                    broll_used += 1
                    last_broll_idx = idx
                    used_broll_queries.add(broll_query)
                    print(f"🎥 Scene {idx}: muted b-roll for {broll_query!r}")
                    continue
                os.remove(video_path)

        # ---- 3) Generated frames fill whatever is still missing ----
        while len(images) < wanted:
            if api_calls:
                time.sleep(THROTTLE_SECONDS)
            shot = len(images)
            path = os.path.join(output_dir, f"lf_scene_{idx:03d}_{shot}.jpg")
            prompt = scene.get("image_prompt") or \
                "wide establishing shot, dark historical scene"
            print(f"🖼️  Scene {idx}: generating shot {shot + 1}/{wanted} "
                  f"(call {api_calls + 1})...")
            if download_image(_vary_prompt(prompt, shot), path,
                              width=LF_W, height=LF_H):
                _to_kb_size(path)
            elif _reuse_real_frame(real_pool, path, len(images) + idx):
                # The generator being down must not turn the video into black
                # screens with subtitles, which is exactly what a run of thirty
                # HTTP 500s produced. A real photograph from elsewhere in the
                # video, punched in so it does not read as the same shot, keeps
                # something honest and watchable on screen.
                print(f"♻️  Scene {idx}: generation failed — re-framed an "
                      f"archival photo instead of a black frame.")
            else:
                print(f"⚠️  Scene {idx}: generation failed — designed fallback.")
                make_fallback_image(path, seed=idx * 10 + shot,
                                    width=LF_W, height=LF_H)
                _to_kb_size(path)
            images.append(path)
            api_calls += 1

        scene["image_paths"] = images
        scene["image_path"] = images[0]
        if credits:
            scene["credit"] = credits

    total_frames = sum(len(s.get("image_paths") or []) for s in scenes)
    print(f"✅ Visuals complete: {total_frames} frames for {len(scenes)} scenes "
          f"({archival_used} archival, {api_calls} generated, "
          f"{broll_used} b-roll clips).")
    return scenes


# Stock footage only exists in the present tense, so the only clips that can
# sit inside a period story are of things that look the same in any century.
# Keyed by subject words in the scene; the query on the right is what is
# actually sent to Pexels. Deliberately absent: people, soldiers, vehicles,
# cities — a story about the 1415 Portuguese invasion force once drew modern
# soldiers at a parade with a national flag in frame, which is exactly the
# clip that costs a channel its credibility.
TIMELESS_BROLL = [
    (("sea", "ocean", "ship", "fleet", "naval", "harbor", "harbour", "strait",
      "sail", "tide", "wave"), "dark rough sea waves"),
    (("storm", "clouds", "thunder", "rain"), "dark storm clouds timelapse"),
    (("fire", "burn", "flame", "smoke", "ash"), "flames burning in darkness"),
    (("map", "chart"), "antique map parchment"),
    (("letter", "document", "order", "treaty", "decree", "archive", "record",
      "ledger", "parchment", "scroll"), "old handwritten letter candlelight"),
    (("castle", "fortress", "wall", "fort", "citadel", "rampart", "gate"),
     "ancient stone fortress wall"),
    (("church", "cathedral", "monastery", "chapel"),
     "cathedral interior candles"),
    (("mountain", "valley", "cliff", "coast"), "misty mountains fog"),
    (("forest", "woods"), "dark foggy forest"),
    (("snow", "winter", "frost", "ice"), "snow falling darkness"),
    (("desert", "dune", "sand"), "desert dunes wind"),
    (("candle", "lantern", "torch", "night"), "candle flame flickering dark"),
]


def _timeless_broll_query(scene, used=()) -> str:
    """The Pexels query for a scene, or None when the scene has no unused
    timeless subject. None is the common and correct case: no b-roll beats
    wrong b-roll, and a repeat of one already on screen is the worst of all —
    'antique map parchment' was picked four times in one video."""
    text = (f"{scene.get('archive_query') or ''} "
            f"{scene.get('image_prompt') or ''}").lower()
    for keys, query in TIMELESS_BROLL:
        if query not in used and any(k in text for k in keys):
            return query
    return None
