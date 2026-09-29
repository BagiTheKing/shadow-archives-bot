"""
One picture per sentence: the Shorts' shot list.

WHY
The first attempt at a faster edit cut each scene into 3.5-second shots of
the SAME painting: wide, then a punch-in, then wide again with another move.
Watched back (run #123, the Carrington Event), it read as the video lagging:
one picture panned three ways, no new information, nothing telling a viewer
what the story is. More cuts only help when every cut shows something new.

WHAT A SHOT IS NOW
The writer gives each scene one shot per sentence: an image prompt showing
exactly what that sentence says, and an archive search when the sentence
names something with a real photograph or period artwork. So:
  - every shot is a different picture, and no picture is used twice;
  - a cut lands where its sentence starts in the narration (word timings),
    so the picture changes when the subject does;
  - each picture gets one slow move (video_engine).

THE BUDGET
Cloudflare's free allowance is about 15 generated frames a day, and a
long-form video uses about 7 of them (run #48: 7 generated of 87 frames, the
rest archive). A Short gets at most SHORTS_MAX_IMAGES pictures; real archive
pictures (shorts_archive) come first and cost nothing, the generator fills
the rest. Over budget, the two shortest neighbouring shots of a scene are
merged, never the hook's.
"""

import os
import re

MAX_IMAGES = int(os.getenv("SHORTS_MAX_IMAGES", "10"))
# A picture on screen for less than this is a flash, not a shot.
MIN_SHOT_SECONDS = 1.8


def sentences(text: str):
    return [s for s in re.split(r"(?<=[.!?])\s+", (text or "").strip()) if s]


def _groups(items, k):
    """items split into k consecutive groups, balanced by word count."""
    k = max(1, min(k, len(items)))
    total = sum(len(s.split()) for s in items) or 1
    groups, current, done = [], [], 0
    for i, s in enumerate(items):
        current.append(s)
        done += len(s.split())
        left_items = len(items) - i - 1
        left_groups = k - len(groups) - 1
        if left_groups > 0 and (done >= total * (len(groups) + 1) / k
                                or left_items == left_groups):
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def sentence_prompt(sentence: str) -> str:
    """An image prompt made from the sentence itself, for a sentence the
    writer gave no picture: the period, the style and "no text" are stamped
    on at generation (visual_engine.build_generation_prompt)."""
    return f"medium shot, one clear focal subject, {sentence.strip(' .')}"


def normalize_shots(data: dict) -> dict:
    """Gives every story scene a "shots" list, in place: one entry per
    sentence where the writer supplied enough prompts, fewer (covering
    consecutive sentences) where it did not. Each shot is
    {"text", "image_prompt", "archive_query"}."""
    for scene in data.get("scenes") or []:
        if scene.get("is_outro"):
            continue
        sents = sentences(scene.get("narration")) or [scene.get("narration") or ""]
        prompts = []
        for shot in scene.get("shots") or []:
            if not isinstance(shot, dict):
                continue
            prompt = (shot.get("image_prompt") or "").strip()
            # the same picture twice in a row is one shot, not two
            if prompt and (not prompts or prompts[-1]["image_prompt"] != prompt):
                prompts.append({"image_prompt": prompt,
                                "archive_query": (shot.get("archive_query") or "").strip()})
        if not prompts:
            prompts = [{"image_prompt": scene.get("image_prompt") or "",
                        "archive_query": (scene.get("archive_query") or "").strip()}]
        # Every sentence gets its own shot. A writer that gave fewer prompts
        # than sentences (run #124: one or two per scene, so pictures held
        # for 9 seconds) has its prompts spread over the sentences in order;
        # a sentence left without one is drawn from its own words, which is
        # what the picture should show anyway.
        groups = _groups(sents, len(prompts))
        scene["shots"] = []
        for prompt, group in zip(prompts, groups):
            scene["shots"].append(dict(prompt, text=group[0]))
            for sentence in group[1:]:
                scene["shots"].append({"image_prompt": sentence_prompt(sentence),
                                       "archive_query": "", "text": sentence})
        scene["image_prompt"] = scene["shots"][0]["image_prompt"]
    return data


def _starts(scene):
    """Local start time of each shot, from the scene's word timings: a shot
    starts on the first word of its first sentence. Falls back to word
    counts spread over the scene when the timings do not line up with the
    narration word for word."""
    shots = scene["shots"]
    words = scene.get("words") or []
    spoken = (scene.get("narration") or "").split()
    offsets, n = [], 0
    for shot in shots:
        offsets.append(n)
        n += len(shot["text"].split())
    if words and len(words) == len(spoken):
        return [float(words[min(o, len(words) - 1)]["start"]) if o else 0.0
                for o in offsets]
    duration = float(scene.get("duration") or 0)
    return [duration * o / max(n, 1) for o in offsets]


def _merge(shots, i):
    """Shot i+1 folds into shot i: its sentence stays, its picture goes."""
    a, b = shots[i], shots[i + 1]
    keep = a if len(a["text"]) >= len(b["text"]) else b
    shots[i] = dict(keep, text=f"{a['text']} {b['text']}", start=a["start"])
    del shots[i + 1]


def time_shots(script_data: dict) -> int:
    """After the narration is rendered: gives every shot its "start" (local
    seconds), merges shots too short to read, and holds the whole video to
    MAX_IMAGES pictures. Returns the number of pictures the video needs."""
    story = [s for s in script_data.get("scenes") or []
             if not s.get("is_outro") and s.get("shots")]
    for scene in story:
        for shot, start in zip(scene["shots"], _starts(scene)):
            shot["start"] = start
        shots = scene["shots"]
        i = 0
        while i < len(shots) - 1:
            end = shots[i + 1]["start"]
            if end - shots[i]["start"] < MIN_SHOT_SECONDS:
                _merge(shots, i)
            else:
                i += 1
        if len(shots) > 1 and (scene["duration"] - shots[-1]["start"]
                               < MIN_SHOT_SECONDS):
            _merge(shots, len(shots) - 2)

    def total():
        return sum(len(s["shots"]) for s in story)

    while total() > MAX_IMAGES:
        # the shortest pair of neighbouring shots anywhere but the hook
        best = None
        for si, scene in enumerate(story):
            if si == 0 and scene is script_data["scenes"][0]:
                continue
            shots = scene["shots"]
            for i in range(len(shots) - 1):
                nxt = shots[i + 2]["start"] if i + 2 < len(shots) else scene["duration"]
                span = nxt - shots[i]["start"]
                if best is None or span < best[0]:
                    best = (span, scene, i)
        if best is None:
            break
        _merge(best[1]["shots"], best[2])

    for scene in story:
        scene["image_prompt"] = scene["shots"][0]["image_prompt"]
    n = total()
    print(f"🎬 Shot list: {n} picture(s) across {len(story)} scenes "
          f"(at most {MAX_IMAGES}), one per sentence where the budget allows.")
    return n
