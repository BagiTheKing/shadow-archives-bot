"""
Long-form video engine — 16:9, built around the Actions minute budget.

The repo is private, so runs are metered against 2,000 minutes a month. A naive
port of the Shorts renderer to eight minutes costs roughly 100 minutes per run.
Four changes bring it to a bit under an hour:

1. NO PER-FRAME SCALE. longform_visual saves frames pre-upscaled, so the
   `scale=` that precedes every Shorts zoompan is gone. This is the big one.
2. CHEAP INTERMEDIATES. Scene clips and chapter reels use -preset veryfast at a
   high bitrate. They are re-encoded by the final pass anyway, so spending
   -preset medium on them buys nothing visible.
3. CUT ON CHAPTER BOUNDARIES. Scenes crossfade within a chapter, but chapters
   butt-join, which lets the chapter reels be concatenated with stream copy
   instead of a second full re-encode. It is also the right edit: a dissolve
   inside a section, a clean cut between them.
4. ONE QUALITY PASS. Grade, captions, music and fades all happen in the single
   final encode.

Captions are deliberately not the Shorts treatment. Word-synced 96pt text
punching in for eight minutes is exhausting; this uses calmer four-word chunks in
a smaller face at the lower third.
"""

import os
import random
import re
import subprocess

W, H = 1920, 1080
FPS = 30
XFADE = 0.5              # crossfade within a chapter
STAT_HOLD = 3.5          # seconds a headline number stays on screen

FONT_NAME = "Anton"
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONTS_DIR = os.path.join(BASE_DIR, "fonts")
MUSIC_DIR = os.path.join(BASE_DIR, "music")

ASS_HEADER = f"""[Script Info]
Title: Archive Paradox Long Form
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
PlayResX: {W}
PlayResY: {H}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Body,{FONT_NAME},54,&H00FFFFFF,&H0000E8FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,3,2,2,140,140,70,1
Style: Stat,{FONT_NAME},170,&H0000E8FF,&H00FFFFFF,&H00000000,&HB0000000,0,0,0,0,100,100,2,0,1,5,4,8,120,120,120,1
Style: StatLabel,{FONT_NAME},46,&H00E8E8E8,&H00FFFFFF,&H00000000,&HB0000000,0,0,0,0,100,100,3,0,1,3,2,8,120,120,330,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

# Crossfade styles, rotated per cut. One transition for seven minutes is the
# single biggest reason a stills-based documentary reads as a slideshow. These
# all suit a dark tone — no pixelize, no wind, nothing that reads as a 90s
# PowerPoint. Cost is identical to plain fade: xfade is already running.
TRANSITIONS = [
    "fade", "smoothleft", "circleopen", "fadeblack", "smoothright",
    "diagtl", "dissolve", "radial", "smoothup", "diagbr",
    "wipeleft", "squeezeh",
]

# Accent used for numbers in captions and for stat callouts (BGR in ASS).
ACCENT_ASS = r"&H0000E8FF&"


def _ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int(round((seconds - int(seconds)) * 100))
    if cs == 100:
        s, cs = s + 1, 0
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def timestamp(seconds: float) -> str:
    """H:MM:SS / M:SS for YouTube chapter markers."""
    seconds = int(seconds)
    h, m, s = seconds // 3600, (seconds % 3600) // 60, seconds % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _chunk_words(words, max_words=4):
    """Four-word chunks, broken early on punctuation. Calmer than the 2-3 word
    pop-in used for Shorts, which is unreadable over eight minutes."""
    chunks, current = [], []
    for w in words:
        current.append(w)
        ends_clause = w["word"].rstrip().endswith((".", ",", "!", "?", ";", ":", "—"))
        if len(current) >= max_words or ends_clause:
            chunks.append(current)
            current = []
    if current:
        chunks.append(current)
    return chunks


def _style_chunk(text: str) -> str:
    """Fades the caption in, and tints any chunk carrying a figure. The numbers
    are the point of this channel, so they should read as the point."""
    body = text.replace("{", "").replace("}", "")
    fx = r"{\fad(140,90)}"
    if re.search(r"\d", body):
        return f"{fx}{{\\c{ACCENT_ASS}}}{body}"
    return f"{fx}{body}"


def generate_subtitles(scenes, output_ass, offsets, stat_cues=None):
    """offsets[i] is the global start time of scene i on the final timeline.
    stat_cues is [(start_seconds, value, label), ...] for the number callouts."""
    print("📝 Building long-form captions...")
    events = []
    for idx, scene in enumerate(scenes):
        offset = offsets[idx]
        words = scene.get("words") or []
        if words:
            chunks = _chunk_words(words)
            for c, chunk in enumerate(chunks):
                start = offset + chunk[0]["start"]
                end = (offset + chunks[c + 1][0]["start"]) if c + 1 < len(chunks) \
                    else (offset + scene["duration"])
                text = " ".join(w["word"] for w in chunk)
                events.append(
                    f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Body,,0,0,0,,"
                    f"{_style_chunk(text)}")
        else:
            dur, sentence = scene["duration"], scene.get("narration", "")
            step = dur / max(1, (len(sentence) // 70) + 1)
            parts = [sentence[i:i + 70] for i in range(0, len(sentence), 70)] or [sentence]
            for p, part in enumerate(parts):
                events.append(
                    f"Dialogue: 0,{_ass_time(offset + p * step)},"
                    f"{_ass_time(offset + (p + 1) * step)},Body,,0,0,0,,{part.strip()}")

    # Number callouts: big accent figure, small label under it, punched in at the
    # top of frame so it never collides with the lower-third captions.
    for start, value, label in (stat_cues or []):
        end = start + STAT_HOLD
        grow = r"{\fad(200,260)\fscx60\fscy60\t(0,220,\fscx100\fscy100)}"
        events.append(
            f"Dialogue: 1,{_ass_time(start)},{_ass_time(end)},Stat,,0,0,0,,{grow}{value}")
        if label:
            events.append(
                f"Dialogue: 1,{_ass_time(start + 0.18)},{_ass_time(end)},"
                f"StatLabel,,0,0,0,,{{\\fad(220,260)}}{label.upper()}")

    with open(output_ass, "w", encoding="utf-8") as f:
        f.write(ASS_HEADER + "\n".join(events))
    print(f"✅ Captions: {len(events)} events "
          f"({len(stat_cues or [])} number callouts)")


def _ken_burns(duration: float, style: int) -> str:
    """Eight motions instead of four, including diagonals and vertical drifts.
    No scale= prefix: longform_visual already saved the frame upscaled."""
    frames = max(int(duration * FPS), 1)
    common = f"d={frames}:s={W}x{H}:fps={FPS}"
    rate = 0.18 / frames
    cx, cy = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    # Horizontal / vertical travel expressions for a held zoom.
    right, left = f"(iw-iw/zoom)*on/{frames}", f"(iw-iw/zoom)*(1-on/{frames})"
    down, up = f"(ih-ih/zoom)*on/{frames}", f"(ih-ih/zoom)*(1-on/{frames})"

    style %= 8
    if style == 0:   # slow push in
        return f"zoompan=z='min(zoom+{rate:.6f},1.18)':x='{cx}':y='{cy}':{common}"
    if style == 1:   # pull out
        return (f"zoompan=z='if(eq(on,1),1.18,max(zoom-{rate:.6f},1.0))'"
                f":x='{cx}':y='{cy}':{common}")
    if style == 2:   # pan right
        return f"zoompan=z='1.12':x='{right}':y='{cy}':{common}"
    if style == 3:   # pan left
        return f"zoompan=z='1.12':x='{left}':y='{cy}':{common}"
    if style == 4:   # drift down
        return f"zoompan=z='1.12':x='{cx}':y='{down}':{common}"
    if style == 5:   # drift up
        return f"zoompan=z='1.12':x='{cx}':y='{up}':{common}"
    if style == 6:   # diagonal, top-left to bottom-right, pushing in
        return (f"zoompan=z='min(zoom+{rate:.6f},1.20)'"
                f":x='{right}':y='{down}':{common}")
    # diagonal, bottom-right to top-left, pulling out
    return (f"zoompan=z='if(eq(on,1),1.20,max(zoom-{rate:.6f},1.0))'"
            f":x='{left}':y='{up}':{common}")


def _run(cmd):
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, check=True)


def _scene_clip(image, audio, duration, out, style):
    _run(["ffmpeg", "-y", "-loop", "1", "-framerate", str(FPS), "-i", image,
          "-i", audio, "-vf", _ken_burns(duration, style),
          "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
          "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2",
          "-pix_fmt", "yuv420p", "-t", f"{duration:.3f}", out])


def _xfade_chapter(clips, durations, out, cut_index=0):
    """Crossfades one chapter's clips, rotating transition style per cut.
    cut_index continues across chapters so the pattern does not restart every
    chapter. Returns (within-chapter offsets, total)."""
    if len(clips) == 1:
        os.replace(clips[0], out)
        return [0.0], durations[0]

    inputs = []
    for c in clips:
        inputs += ["-i", c]
    offsets, t = [0.0], 0.0
    for i in range(len(clips) - 1):
        t += durations[i] - XFADE
        offsets.append(t)

    fc, vprev, aprev = [], "[0:v]", "[0:a]"
    n = len(clips)
    for i in range(1, n):
        vout = f"[v{i}]" if i < n - 1 else "[vout]"
        aout = f"[a{i}]" if i < n - 1 else "[aout]"
        trans = TRANSITIONS[(cut_index + i - 1) % len(TRANSITIONS)]
        fc.append(f"{vprev}[{i}:v]xfade=transition={trans}:duration={XFADE}"
                  f":offset={offsets[i]:.3f}{vout}")
        fc.append(f"{aprev}[{i}:a]acrossfade=d={XFADE}{aout}")
        vprev, aprev = vout, aout

    _run(["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(fc),
          "-map", "[vout]", "-map", "[aout]",
          "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
          "-c:a", "aac", "-b:a", "160k", "-pix_fmt", "yuv420p", out])
    return offsets, sum(durations) - XFADE * (n - 1)


def _pick_music():
    if not os.path.isdir(MUSIC_DIR):
        return None
    tracks = [f for f in os.listdir(MUSIC_DIR)
              if f.lower().endswith((".mp3", ".wav", ".m4a", ".ogg"))]
    return os.path.join(MUSIC_DIR, sorted(tracks)[random.randrange(len(tracks))]) \
        if tracks else None


def generate_longform_video(scenes, build_dir="build"):
    """Renders the finished video. Returns (path, total_seconds, chapter_marks)
    where chapter_marks is [(heading, start_seconds), ...] for the description."""
    print("🎬 Long-form video engine starting...")
    usable = [s for s in scenes if s.get("image_path") and s.get("audio_path")]

    # ---- Phase 1: scene clips ----
    for idx, s in enumerate(usable):
        s["_clip"] = os.path.join(build_dir, f"lf_clip_{idx:03d}.mp4")
        print(f"🎞️  Clip {idx + 1}/{len(usable)} ({s['duration']:.1f}s)...")
        _scene_clip(s["image_path"], s["audio_path"], s["duration"], s["_clip"], idx % 4)

    # ---- Phase 2: one reel per chapter, crossfaded inside ----
    chapters, current, current_idx = [], [], None
    for s in usable:
        c = s.get("chapter_index", 0)
        if c != current_idx:
            if current:
                chapters.append((current_idx, current))
            current, current_idx = [], c
        current.append(s)
    if current:
        chapters.append((current_idx, current))

    reels, global_offsets, chapter_marks, cursor, cuts = [], [], [], 0.0, 0
    stat_cues = []
    for c_idx, group in chapters:
        reel = os.path.join(build_dir, f"lf_reel_{c_idx:02d}.mp4")
        print(f"🔗 Chapter {c_idx + 1}: crossfading {len(group)} scenes...")
        offs, total = _xfade_chapter([g["_clip"] for g in group],
                                     [g["duration"] for g in group], reel,
                                     cut_index=cuts)
        cuts += max(0, len(group) - 1)
        chapter_marks.append((group[0].get("chapter_heading", f"Part {c_idx + 1}"), cursor))
        for g, o in zip(group, offs):
            global_offsets.append(cursor + o)

        # A chapter's headline number, held over whatever frame is on screen.
        # Costs nothing: it rides the caption pass that already runs.
        stat = group[0].get("key_stat") or {}
        if stat.get("value") and total > STAT_HOLD + 4:
            stat_cues.append((cursor + total * 0.45, str(stat["value"]),
                              str(stat.get("label", ""))))

        cursor += total
        reels.append(reel)

    total_duration = cursor

    # ---- Phase 3: join chapters with stream copy (chapters butt-join) ----
    merged = os.path.join(build_dir, "lf_merged.mp4")
    if len(reels) == 1:
        os.replace(reels[0], merged)
    else:
        listfile = os.path.join(build_dir, "reels.txt")
        with open(listfile, "w", encoding="utf-8") as f:
            for r in reels:
                f.write(f"file '{os.path.abspath(r).replace(os.sep, '/')}'\n")
        print("🧵 Concatenating chapters (stream copy)...")
        _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-fflags", "+genpts",
              "-i", listfile, "-c", "copy", merged])

    # ---- Phase 4: single quality pass — grade, captions, music, fades ----
    ass_path = os.path.join(build_dir, "lf_subtitles.ass")
    generate_subtitles(usable, ass_path, global_offsets, stat_cues=stat_cues)

    print("🔥 Final render: grade, captions, music...")
    final_output = os.path.join(build_dir, "final_longform.mp4")
    safe_ass = os.path.abspath(ass_path).replace("\\", "/").replace(":", "\\:")
    safe_fonts = os.path.abspath(FONTS_DIR).replace("\\", "/").replace(":", "\\:")

    vf = ("vignette=PI/5,"
          "eq=contrast=1.04:saturation=0.94,"
          f"ass='{safe_ass}':fontsdir='{safe_fonts}',"
          f"fade=t=in:st=0:d=0.8,"
          f"fade=t=out:st={max(total_duration - 1.0, 0):.2f}:d=1.0")

    music = _pick_music()
    if music:
        print(f"🎵 Bed: {os.path.basename(music)}")
        af = (f"[1:a]aloop=loop=-1:size=2e9,atrim=0:{total_duration:.3f},"
              f"volume=0.10,afade=t=out:st={max(total_duration - 2.5, 0):.2f}:d=2.5[bg];"
              f"[0:a][bg]amix=inputs=2:duration=first:normalize=0,"
              f"afade=t=out:st={max(total_duration - 1.2, 0):.2f}:d=1.2[aout]")
        cmd = ["ffmpeg", "-y", "-i", merged, "-i", music,
               "-filter_complex", af, "-map", "0:v", "-map", "[aout]", "-vf", vf,
               "-c:v", "libx264", "-preset", "medium", "-crf", "21",
               "-c:a", "aac", "-b:a", "192k", final_output]
    else:
        cmd = ["ffmpeg", "-y", "-i", merged, "-vf", vf,
               "-c:v", "libx264", "-preset", "medium", "-crf", "21",
               "-c:a", "copy", final_output]
    _run(cmd)

    print(f"✅ Long-form render complete: {final_output} "
          f"({total_duration / 60:.1f} min, {len(chapter_marks)} chapters)")
    return final_output, total_duration, chapter_marks
