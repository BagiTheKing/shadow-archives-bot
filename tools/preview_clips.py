"""
Side-by-side clip preview: the clips of one finished script cut the current
way (longform_clips_old.py, main's copy) and the proposed way
(longform_clips.py), from the same rendered scenes. Run by
.github/workflows/preview_clips.yml; nothing is uploaded.

    python tools/preview_clips.py script.json out_dir
"""

import json
import os
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

BUILD = "build"


def main(script_path, out):
    from audio_engine import generate_audio_pipeline
    from longform_llm import flatten_scenes
    from longform_video import generate_longform_video
    from longform_visual import generate_longform_visuals
    import longform_clips as new
    import longform_clips_old as old

    os.makedirs(BUILD, exist_ok=True)
    os.makedirs(out, exist_ok=True)
    script = json.load(open(script_path, encoding="utf-8"))
    scenes = flatten_scenes(script)
    # the chapters the clips come from (opening, characters, decision, the
    # start of the aftermath), not the whole video: half the render time
    keep, per = [], {}
    for s in scenes:
        c = s["chapter_index"]
        per[c] = per.get(c, 0) + 1
        if s.get("chapter_role") == "close":
            continue
        if s.get("chapter_role") == "drive" and per[c] > 12:
            continue
        keep.append(s)
    print(f"🎬 Preview: {len(keep)} of {len(scenes)} scenes.")
    keep = generate_audio_pipeline({"scenes": keep}, BUILD)["scenes"]
    keep, _, _ = generate_longform_visuals(keep, BUILD, topic=script.get("topic"))
    generate_longform_video(keep, BUILD)

    for name, mod in (("old", old), ("new", new)):
        clips = mod.make_clips(keep, script, BUILD, limit=4)
        for k, c in enumerate(clips):
            dest = os.path.join(out, f"{name}_{k + 1}.mp4")
            shutil.move(c["path"], dest)
            print(f"   {name} {k + 1}: {c['label']}, {c['length']:.1f}s -> {dest}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
