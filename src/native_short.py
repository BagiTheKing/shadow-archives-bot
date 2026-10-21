"""
One Short made for the phone, on the long-form video's story (owner,
2026-10-21). It replaces the clips cut out of the long video: a 16:9 film
squeezed into a 9:16 frame kept its crossfades, its wipes and its wide
pictures, and no amount of cropping hid them ("these transitions are making
me crazy").

What it is, from what the clip previews taught (20-21 Oct):
- the news first, on real footage of it (DVIDS), with the video's title;
- the narrator's promise, and a large line on screen backing it up
  (longform_clips.PROMISES), never a length and never an ask;
- four to six beats of the story, each one sentence with ONE picture chosen
  by what the sentence is about: where -> a map, who -> that person's photo,
  what happened -> a real photo or footage of it, a mood -> a painting, the
  last resort;
- every picture made or cropped for 9:16, hard cuts on the sentence, no
  crossfade, no box, no wipe;
- a dip to black, then the story's paradox in the voice alone, and the
  signature line heard, never written.

Facts come only from the long-form script: every figure and capitalised
name in a beat must already be in it.
"""

import json
import os
import re
import subprocess

W, H, FPS = 1080, 1920, 30
DIP = 0.35                        # the story fades to black before the paradox
LEAD = 0.4                        # silence before the paradox is spoken
GAP = 0.12                        # breath between two beats
MUSIC_LEVEL = 0.08
KINDS = ("map", "person", "photo", "footage", "art")


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def _source_text(script):
    return " ".join(s.get("narration") or "" for c in script.get("chapters") or []
                    for s in c.get("scenes") or []) + " " + (script.get("title") or "")


def grounded(line, script) -> bool:
    """Every figure and every capitalised name in `line` is in the script."""
    source = _source_text(script).lower()
    for num in re.findall(r"\d[\d,.]*", line):
        if num.strip(".,") not in source:
            return False
    for w in line.split()[1:]:
        name = re.sub(r"[^\w'-]", "", w)
        if name[:1].isupper() and name.lower() not in source:
            return False
    return True


def write_beats(script):
    """[{"line", "kind", "subject", "prompt"}] or None: the story in four to
    six spoken sentences, each with the one picture it needs."""
    from llm_provider import get_pool
    from longform_llm import _call
    paradox = (script.get("paradox") or {}).get("answer", "")
    places = ", ".join(script.get("places") or [])
    prompt = (
        "Below is a documentary script. Write the middle of a 30-second "
        "vertical Short about it: 4 to 6 sentences, 55 to 90 words in all, "
        "that tell the story from its start to the moment just before this "
        f"twist (which is said after them, so do NOT say it): \"{paradox}\"\n"
        "Each sentence: one idea, at most 20 words, plain words, past tense, "
        "names and places a stranger can follow. Only facts stated in the "
        "script, no new names, numbers or dates.\n"
        "For each sentence choose the ONE picture it needs, by what it is "
        "about:\n"
        "- where (a place, a border, troops moving): kind \"map\", subject = "
        f"one place from: {places}\n"
        "- who (a named person, their decision): kind \"person\", subject = "
        "that person's full name exactly as in the script\n"
        "- what happened (an event, a crowd, a battle, damage): kind "
        "\"photo\" or \"footage\", subject = 2-5 words to search an archive "
        "with, naming the place and year\n"
        "- a mood or an idea with nothing physical to show: kind \"art\", "
        "prompt = one concrete scene to paint (no text, no flags)\n"
        "Use at least one map and at least one person when the story has "
        "them. Never the same kind three times in a row.\n\n"
        f"SCRIPT:\n{_source_text(script)[:12000]}\n\n"
        'Reply as {"beats": [{"line": "...", "kind": "...", "subject": "...", '
        '"prompt": "..."}]}')
    try:
        data = _call(get_pool(), "You write tight documentary Shorts. Output only JSON.",
                     prompt, max_tokens=1500)
    except Exception as e:
        print(f"⚠️ Native Short not written ({type(e).__name__}: {e}).")
        return None
    beats = (data or {}).get("beats") if isinstance(data, dict) else None
    out = []
    for b in beats or []:
        line = re.sub(r"\s+", " ", str(b.get("line") or "")).strip()
        kind = str(b.get("kind") or "art").lower()
        if not line or len(line.split()) > 24:
            continue
        if not grounded(line, script):
            print(f"   ↩️  Beat dropped (not in the script): {line!r}")
            continue
        out.append({"line": line, "kind": kind if kind in KINDS else "art",
                    "subject": str(b.get("subject") or "").strip(),
                    "prompt": str(b.get("prompt") or line).strip()})
    words = sum(len(b["line"].split()) for b in out)
    if not (3 <= len(out) <= 7) or words < 40:
        print(f"⚠️ Native Short: {len(out)} usable beat(s), {words} words — not made.")
        return None
    return out


# ---------------------------------------------------------------------------
# Pictures, all 9:16
# ---------------------------------------------------------------------------

def _vertical_fit(img, width, height):
    """Cover-crop to width x height, centred on the faces in the picture
    (else its middle)."""
    from PIL import Image
    x_focus = 0.5
    try:
        import cv2
        import numpy as np
        from cast_engine import _faces, faces_available
        if faces_available():
            faces = _faces(cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR))[:3]
            if faces:
                x_focus = sum(f[0] + f[2] / 2 for f in faces) / len(faces) / img.width
    except Exception:
        pass
    scale = max(width / img.width, height / img.height)
    img = img.resize((max(width, int(img.width * scale) + 1),
                      max(height, int(img.height * scale) + 1)), Image.LANCZOS)
    x = int(min(max(x_focus * img.width - width / 2, 0), img.width - width))
    y = int((img.height - height) * 0.35)
    return img.crop((x, y, x + width, y + height))


def _photo(subject, era, path, person=False):
    """(path, credit) of a real photo of `subject`, or None."""
    if not subject:
        return None
    from archive_engine import download_archival_image, format_credit, search_archives
    from longform_visual import _era_appropriate
    from media_sources import relevant
    for cand in search_archives(subject):
        if not _era_appropriate(cand, era):
            continue
        # a person is that person or nobody: every part of the name in the
        # title (the officer's portrait that stood in for Yemen's leaders)
        if person and not relevant(cand, subject):
            continue
        if download_archival_image(cand, path, W, H, fit=_vertical_fit):
            return path, format_credit(cand)
    return None


def _footage(subject, era, path, seconds):
    if not subject:
        return None
    from archive_engine import fetch_commons_video, format_credit, search_commons_video
    from media_sources import search_dvids
    cands = search_commons_video(subject, era=era)[:4]
    cands += [c for c in search_dvids(subject, "video", 4)
              if not era or not c.get("year") or abs(c["year"] - era) <= 2]
    for cand in cands:
        if fetch_commons_video(cand, path, seconds=seconds + 0.5):
            return path, format_credit(cand)
    return None


def _map(subject, script, beat, path, seconds):
    """A vertical map of `subject`: the long-form script's own map plan for
    that place when it has one (its origin, arrow and lit regions), else the
    place marked."""
    if not subject:
        return None
    import map_engine
    plan = None
    for c in script.get("chapters") or []:
        for s in c.get("scenes") or []:
            m = s.get("map") or {}
            names = [m.get("target") or ""] + list(m.get("light") or [])
            if m.get("target") and any(subject.lower() in n.lower() or n.lower() in subject.lower()
                                       for n in names if n):
                plan = m
                break
        if plan:
            break
    plan = plan or {"target": subject, "action": "mark"}
    old = (map_engine.W, map_engine.H)
    map_engine.W, map_engine.H = W, H
    try:
        got = map_engine.render_map_scene(
            {"map": plan, "words": beat.get("words") or [],
             "era_year": script.get("era_year"), "chapter_role": "build"},
            path, seconds)
    finally:
        map_engine.W, map_engine.H = old
    return (got, "") if got else None


def _art(prompt, era, path):
    from visual_engine import download_image
    try:
        if download_image(prompt, path, width=W, height=H, era_year=era):
            return path, ""
    except Exception as e:
        print(f"   ↩️  Painting failed ({type(e).__name__}: {e}).")
    return None


def picture_for(beat, script, k, build_dir, seconds):
    """Fills beat["still"] or beat["video"] (and "credit"): its own kind
    first, then a real photo of its subject, then a painting."""
    era = script.get("era_year")
    base = os.path.join(build_dir, f"ns_{k:02d}")
    tries = {
        "map": lambda: _map(beat["subject"], script, beat, base + "_map.mp4", seconds),
        "person": lambda: _photo(beat["subject"], era, base + ".jpg", person=True),
        "photo": lambda: _photo(beat["subject"], era, base + ".jpg"),
        "footage": lambda: _footage(beat["subject"], era, base + "_f.mp4", seconds),
        "art": lambda: _art(beat["prompt"], era, base + "_art.jpg"),
    }
    order = [beat["kind"]] + [k2 for k2 in ("photo", "art") if k2 != beat["kind"]]
    for kind in order:
        try:
            got = tries[kind]()
        except Exception as e:
            print(f"   ↩️  Beat {k} {kind} failed ({type(e).__name__}: {e}).")
            got = None
        if got:
            path, credit = got
            beat["video" if path.endswith(".mp4") else "still"] = path
            beat["credit"], beat["used"] = credit, kind
            print(f"🖼️  Beat {k} ({kind}): {beat['line'][:60]!r}")
            return True
    return False


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _beat_clip(beat, k, seconds, out):
    """One beat's picture, silent, exactly `seconds` long, 1080x1920."""
    from video_engine import _ken_burns_filter
    frames = max(1, round(seconds * FPS))
    if beat.get("video"):
        vf = (f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
              f"fps={FPS},setsar=1,trim=end_frame={frames}")
        inputs = ["-stream_loop", "-1", "-i", beat["video"]]
    else:
        vf = (f"{_ken_burns_filter(seconds, k % 2)},setsar=1,"
              f"trim=end_frame={frames}")
        inputs = ["-loop", "1", "-framerate", str(FPS), "-i", beat["still"]]
    subprocess.run(["ffmpeg", "-y", *inputs, "-vf", vf + ",format=yuv420p", "-an",
                    "-c:v", "libx264", "-preset", "medium", "-crf", "19",
                    "-r", str(FPS), "-frames:v", str(frames), out],
                   stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, check=True)


def render(beats, promise, out_path, build_dir):
    """Concatenates the beats with hard cuts, dips to black before the last
    one, and lays narration, captions, the promise line and music."""
    from longform_clips import _headline_events
    from music_attribution import attribution_for
    from video_engine import FONTS_DIR, _pick_music, generate_subtitles

    # timeline: each beat lasts its voice plus a breath; the paradox starts
    # after a short silence
    offsets, t = [], 0.0
    for i, b in enumerate(beats):
        lead = LEAD if b.get("paradox") else 0.0
        b["seconds"] = lead + b["duration"] + (GAP if i < len(beats) - 1 else 1.0)
        b["lead"] = lead
        offsets.append(t + lead)
        t += b["seconds"]
    length = t

    clips = []
    for i, b in enumerate(beats):
        clip = os.path.join(build_dir, f"ns_clip_{i:02d}.mp4")
        _beat_clip(b, i, b["seconds"], clip)
        clips.append(clip)

    # narration: each beat's audio at its offset
    ins, amix = [], []
    for i, b in enumerate(beats):
        ins += ["-i", b["audio_path"]]
        ms = int(offsets[i] * 1000)
        amix.append(f"[{i}:a]aformat=channel_layouts=stereo:sample_rates=44100,"
                    f"adelay={ms}|{ms}[n{i}]")
    voice = os.path.join(build_dir, "ns_voice.m4a")
    subprocess.run(["ffmpeg", "-y", *ins, "-filter_complex",
                    ";".join(amix) + ";" + "".join(f"[n{i}]" for i in range(len(beats)))
                    + f"amix=inputs={len(beats)}:normalize=0,apad,atrim=0:{length:.3f}[a]",
                    "-map", "[a]", "-c:a", "aac", "-b:a", "192k", voice],
                   stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, check=True)

    # captions: the paradox's words only, never the signature line
    scenes = []
    for b in beats:
        words = b.get("words") or []
        if b.get("paradox"):
            words = words[:len(b["line"].split())]
        scenes.append({"narration": b["line"], "words": words,
                       "duration": b["seconds"] - b["lead"]})
    ass = os.path.join(build_dir, "ns.ass")
    generate_subtitles({"scenes": scenes}, ass, timeline_offsets=offsets,
                       total_duration=length, cta_text="")
    reveal_at = offsets[-1] - beats[-1]["lead"]
    with open(ass, "a", encoding="utf-8") as f:
        f.write("\n" + "\n".join(_headline_events("", length, reveal_at, promise)))

    safe_ass = os.path.abspath(ass).replace("\\", "/").replace(":", "\\:")
    safe_fonts = os.path.abspath(FONTS_DIR).replace("\\", "/").replace(":", "\\:")
    graph, labels = [], ""
    for i, b in enumerate(beats):
        fx = ""
        if i == len(beats) - 2:
            fx = f",fade=t=out:st={b['seconds'] - DIP:.3f}:d={DIP}"
        elif i == len(beats) - 1:
            fx = f",fade=t=in:d={DIP}"
        graph.append(f"[{i}:v]setpts=PTS-STARTPTS{fx}[v{i}]")
        labels += f"[v{i}]"
    n = len(beats)
    graph.append(f"{labels}concat=n={n}:v=1:a=0,ass='{safe_ass}':fontsdir='{safe_fonts}',"
                 f"fade=t=out:st={max(length - 0.4, 0):.2f}:d=0.4[v]")
    music = _pick_music()
    inputs = sum((["-i", c] for c in clips), []) + ["-i", voice]
    if music:
        inputs += ["-i", music]
        graph.append(f"[{n + 1}:a]aloop=loop=-1:size=2e9,atrim=0:{length:.3f},"
                     f"volume={MUSIC_LEVEL},afade=t=in:d=0.6,"
                     f"afade=t=out:st={max(length - 1.2, 0):.2f}:d=1.2[m];"
                     f"[{n}:a][m]amix=inputs=2:duration=first:normalize=0[a]")
    else:
        graph.append(f"[{n}:a]anull[a]")
    subprocess.run(["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(graph),
                    "-map", "[v]", "-map", "[a]", "-r", str(FPS),
                    "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                    "-c:a", "aac", "-b:a", "192k", "-pix_fmt", "yuv420p",
                    "-t", f"{length:.3f}", out_path],
                   stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, check=True)
    return length, (attribution_for(music) if music else "")


# ---------------------------------------------------------------------------
# The whole Short
# ---------------------------------------------------------------------------

def make_native_short(script, build_dir="build"):
    """{"path", "length", "title", "description", "comment"} or None. Never
    raises: the long-form video is the run's deliverable."""
    try:
        return _make(script, os.path.join(build_dir, "native"))
    except Exception as e:
        print(f"⚠️ Native Short failed ({type(e).__name__}: {e}).")
        return None


def _make(script, build_dir):
    from audio_engine import generate_audio_pipeline
    from longform_clips import SIGNATURE, find_paradox, news_footage, promise_for
    os.makedirs(build_dir, exist_ok=True)
    paradox = find_paradox(script)
    if not paradox:
        print("⚠️ Native Short: the story has no paradox to end on — not made.")
        return None
    body = write_beats(script)
    if not body:
        return None
    spoken, on_screen = promise_for(script)
    title = re.sub(r"\s+", " ", script.get("title") or "").strip()
    beats = ([{"line": f"{title.rstrip('.')}. {spoken}", "kind": "news",
               "subject": "", "prompt": ""}]
             + body
             + [{"line": paradox["answer"], "kind": "art", "paradox": True,
                 "subject": "", "prompt": body[-1]["prompt"],
                 "say": f"{paradox['answer'].rstrip('.')}. {SIGNATURE}"}])

    # voice first: every picture is cut to its sentence's length
    scenes = [{"narration": b.get("say") or b["line"]} for b in beats]
    generate_audio_pipeline({"scenes": scenes}, build_dir)
    for b, s in zip(beats, scenes):
        if not s.get("audio_path"):
            print("⚠️ Native Short: a line could not be voiced — not made.")
            return None
        b.update(audio_path=s["audio_path"], duration=s["duration"], words=s["words"])

    credits = []
    news = news_footage(script, beats[0]["duration"] + 0.5, build_dir)
    if news:
        beats[0].update(video=news[0], credit=news[1], used="news")
        credits.append(f"Opening footage: {news[1]}")
    else:
        beats[0]["kind"], beats[0]["subject"] = "photo", (script.get("places") or [""])[0]
        beats[0]["prompt"] = body[0]["prompt"]
    for k, b in enumerate(beats):
        if b.get("video") or b.get("still"):
            continue
        if b.get("paradox"):
            # the paradox over the story's strongest real picture, else a
            # painting of its last beat
            prev = [x for x in beats[1:-1] if x.get("used") in ("photo", "person", "footage")]
            if prev:
                b.update(still=prev[-1].get("still"), video=prev[-1].get("video"))
                continue
        if not picture_for(b, script, k, build_dir, b["duration"] + 1.5):
            # Nothing found or generated (the image quota spent, 21 Oct):
            # the story's main place on a map, else the last real picture
            # held a little longer. Never a missing Short for one line.
            place = (script.get("places") or [""])[0]
            got = _map(place, script, b, os.path.join(build_dir, f"ns_{k:02d}_fb.mp4"),
                       b["duration"] + 1.5) if place else None
            if got:
                b.update(video=got[0], used="map")
            else:
                prev = next((x for x in reversed(beats[:k]) if x.get("still") or x.get("video")),
                            None)
                if not prev:
                    print(f"⚠️ Native Short: no picture for {b['line'][:50]!r} — not made.")
                    return None
                b.update(still=prev.get("still"), video=prev.get("video"), used="held")
            print(f"   ↩️  Beat {k}: no picture of its own — {b['used']}.")
        if b.get("credit"):
            credits.append(b["credit"])

    out = os.path.join(build_dir, "native_short.mp4")
    length, music_credit = render(beats, on_screen, out, build_dir)
    print(f"📱 Native Short: {length:.1f}s, {len(beats)} beats "
          f"({', '.join(b.get('used') or b['kind'] for b in beats)}) — {out}")

    description = "\n\n".join(p for p in [
        f"{title}.",
        "Pictures: " + "; ".join(dict.fromkeys(c for c in credits if c)) if credits else "",
        music_credit, "#shorts #history #documentary"] if p)[:4900]
    with open(os.path.join(build_dir, "native_short.json"), "w", encoding="utf-8") as f:
        json.dump({"beats": [{k: b.get(k) for k in ("line", "kind", "used", "subject")}
                             for b in beats]}, f, indent=1)
    return {"path": out, "length": length, "title": f"{title[:91]} #shorts",
            "description": description,
            "comment": (script.get("comment_question") or "").strip()}
