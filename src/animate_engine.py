"""
Image-to-video for a few AI-generated frames (owner, 2026-10-14).

A Wan 2.2 Space on Hugging Face (ANIMATE_SPACE) turns one generated still
into motion as long as its scene (up to 10 s), on the free ZeroGPU quota of the account behind
HF_TOKEN: a handful of shots a day, so they are spent where they count.

The rules, in order:
- real photographs and footage always win: only a scene still showing a
  generated frame is animated (no archival photo, no footage, no map, no
  cast card);
- the hook first, then the rising action; never the payoff (the decision,
  its consequences, the verdict);
- at most ANIMATE_MAX shots per run; the first refusal (quota, queue, a
  Space that is down) ends animation for the run, and the scene keeps its
  usual Ken Burns. Nothing here can fail a video.
"""

import inspect
import os
import re
import shutil

ANIMATE_SPACE = os.getenv("ANIMATE_SPACE", "kulkas2pintu/wan777")
# One a run on the free tier: a 9-second shot reserves ~290 s of GPU, about
# the whole free daily ZeroGPU allowance (14 Oct). Hugging Face PRO (40 min
# a day) would carry ~8; raise ANIMATE_MAX then.
ANIMATE_MAX = int(os.getenv("ANIMATE_MAX", "1"))
# The Space renders 0.5-10 s. An animated scene is animated for its whole
# length (owner, 2026-10-14: no frozen frame after the motion), so only
# scenes that fit are picked; the renderer stretches the clip by at most
# MAX_STRETCH to land on the exact length.
MAX_SECONDS = 10.0
MAX_STRETCH = 1.12
# where the story is told: the hook, then the rising action
ROLE_ORDER = ("open", "build")


def available() -> bool:
    return bool(os.getenv("HF_TOKEN")) and ANIMATE_MAX > 0


def clip_seconds(scene) -> float:
    """How long the scene is on screen (narration plus the dissolve)."""
    from longform_video import XFADE
    return float(scene.get("duration") or 0) + XFADE


def _generated(scene) -> bool:
    return (scene.get("image_path") and not scene.get("is_real")
            and 0 < clip_seconds(scene) <= MAX_SECONDS * MAX_STRETCH
            and not scene.get("video_path") and not scene.get("map")
            and not scene.get("dossier") and not scene.get("_is_map"))


def pick_scenes(scenes, limit=ANIMATE_MAX):
    """Indexes of the scenes to animate: the hook's first generated frame,
    then the first generated frame of each rising-action chapter, in order."""
    picked, seen_chapters = [], set()
    for role in ROLE_ORDER:
        for i, s in enumerate(scenes):
            if len(picked) >= limit:
                return picked
            if s.get("chapter_role") != role or not _generated(s):
                continue
            chapter = s.get("chapter_index", s.get("chapter"))
            if role == "build" and chapter in seen_chapters:
                continue
            seen_chapters.add(chapter)
            picked.append(i)
            if role == "open":
                break
    return picked


def motion_prompt(image_prompt: str) -> str:
    """The scene's subject, without the style and era boilerplate the still
    carried, plus gentle motion. Wan keeps the painted look on its own."""
    subject = re.split(r",\s*(set in the year|historically accurate|dramatic "
                       r"chiaroscuro|dark moody)", image_prompt or "", maxsplit=1)[0]
    subject = re.sub(r"^(wide|medium|close-up|close up|extreme close-up)"
                     r"( shot)?,\s*", "", subject.strip(), flags=re.I)
    return (f"{subject}. Subtle natural movement, people and smoke move "
            "slowly, the camera pushes in gently, no new objects, no text.")


def _client():
    from gradio_client import Client
    key = "token" if "token" in inspect.signature(Client).parameters else "hf_token"
    return Client(ANIMATE_SPACE, **{key: os.environ["HF_TOKEN"]})


def animate(image_path, prompt, out_path, seconds, client=None) -> bool:
    from gradio_client import handle_file
    client = client or _client()
    got = client.predict(input_image=handle_file(image_path), last_image=None,
                         prompt=prompt, duration_seconds=min(seconds, MAX_SECONDS),
                         api_name="/generate_video")
    path = got[0] if isinstance(got, (list, tuple)) else got
    if isinstance(path, dict):
        path = path.get("video") or path.get("path")
    if not path or not os.path.exists(path):
        return False
    shutil.copy(path, out_path)
    return True


def animate_scenes(scenes, output_dir):
    """Adds anim_path to the chosen scenes. Returns how many were animated."""
    if not available():
        return 0
    picks = pick_scenes(scenes)
    if not picks:
        print("🎬 Animation: no generated frame in the hook or rising action "
              "(all real photos or footage) — nothing to animate.")
        return 0
    try:
        client = _client()
    except Exception as e:
        print(f"🎬 Animation skipped: the Space is unreachable ({type(e).__name__}: {e}).")
        return 0
    done = 0
    for i in picks:
        s = scenes[i]
        out = os.path.join(output_dir, f"lf_anim_{i:03d}.mp4")
        try:
            ok = animate(s["image_path"], motion_prompt(s.get("image_prompt")), out,
                         round(clip_seconds(s), 1), client)
        except Exception as e:
            print(f"🎬 Animation stopped at scene {i} ({type(e).__name__}: "
                  f"{str(e)[:160]}) — the rest keep their Ken Burns.")
            break
        if ok:
            s["anim_path"] = out
            done += 1
            print(f"🎬 Scene {i} ({s.get('chapter_role')}): animated.")
    return done
