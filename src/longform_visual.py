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
import time

from PIL import Image

from archive_engine import (search_commons, download_archival_image,
                            format_credit, pexels_available, fetch_broll_video,
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

    for idx, scene in enumerate(scenes):
        wanted = _shots_for(scene.get("duration", 0))
        images, credits = [], []

        # ---- 1) Real archival photographs from Wikimedia Commons ----
        query = (scene.get("archive_query") or "").strip()
        if query:
            candidates = search_commons(query)
            for cand in candidates:
                if len(images) >= wanted:
                    break
                path = os.path.join(output_dir,
                                    f"lf_scene_{idx:03d}_{len(images)}.jpg")
                if download_archival_image(cand, path, KB_W, KB_H):
                    images.append(path)
                    credits.append(format_credit(cand))
            if images:
                archival_used += len(images)
                scene["is_real"] = True
                print(f"📜 Scene {idx}: {len(images)} archival photo(s) "
                      f"for {query!r}")

        # ---- 2) Muted stock b-roll for atmospheric scenes (optional) ----
        if (not images and pexels_available() and broll_used < MAX_BROLL_CLIPS
                and scene.get("duration", 0) >= MIN_BROLL_SCENE_SECONDS):
            broll_query = query or _broll_query_from_prompt(
                scene.get("image_prompt", ""))
            video_path = os.path.join(output_dir, f"lf_broll_{idx:03d}.mp4")
            if broll_query and fetch_broll_video(broll_query, video_path,
                                                 min_duration=4.0):
                poster = os.path.join(output_dir, f"lf_scene_{idx:03d}_0.jpg")
                if extract_poster_frame(video_path, poster, KB_W, KB_H):
                    scene["video_path"] = video_path
                    scene["image_path"] = poster
                    scene["image_paths"] = [poster]
                    broll_used += 1
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


_BROLL_STOPWORDS = {"extreme", "close-up", "closeup", "wide", "establishing",
                    "shot", "low-angle", "overhead", "silhouette", "view",
                    "of", "the", "a", "an", "over-the-shoulder", "dramatic"}


def _broll_query_from_prompt(image_prompt: str) -> str:
    """First few meaningful words of the image prompt — enough for a stock
    search ('stormy sea at night', 'old documents on a desk')."""
    words = [w.strip(",.") for w in image_prompt.split()[:12]
             if w.strip(",.").lower() not in _BROLL_STOPWORDS]
    return " ".join(words[:4])
