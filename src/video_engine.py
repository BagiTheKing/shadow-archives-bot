"""
Video engine — the editing upgrade.

What changed vs the old version:
1. CAPTIONS: word-synced chunks (2-3 words) at the bottom third with a pop-in
   animation, instead of a full paragraph parked in the center of the screen.
2. MOTION: each scene gets a different Ken Burns move (zoom in / zoom out /
   pan left / pan right), with the source upscaled first to kill zoompan jitter.
3. TRANSITIONS: 0.4s crossfades between scenes instead of hard cuts.
4. GRADE: subtle vignette + film grain for a cohesive cinematic look.
5. MUSIC: if a track exists in music/, it's looped, ducked -18dB under the
   narration, and faded out at the end.
6. Output is true 1080x1920 30fps.
"""

import os
import random
import subprocess

W, H = 1080, 1920
FPS = 30
XFADE = 0.4  # crossfade duration in seconds

FONT_NAME = "Anton"
FONTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fonts"
)
MUSIC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "music"
)

ASS_HEADER = f"""[Script Info]
Title: Shadow Archives Captions
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
PlayResX: {W}
PlayResY: {H}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Word,{FONT_NAME},96,&H00FFFFFF,&H0000E8FF,&H00000000,&HA0000000,-1,0,0,0,100,100,1,0,1,7,4,2,60,60,430,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
# Alignment=2 -> bottom-center; MarginV=430 keeps text above the Shorts UI
# (like/comment buttons, title overlay) but well below screen center.


def _ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int(round((seconds - int(seconds)) * 100))
    if cs == 100:
        s, cs = s + 1, 0
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _chunk_words(words, max_words=3):
    """Group word timings into caption chunks of <=3 words, breaking early on
    punctuation so chunks follow the rhythm of the sentence."""
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


def generate_subtitles(script_data: dict, output_ass: str, timeline_offsets: list):
    """Builds word-synced pop-in captions. timeline_offsets[i] is the global
    start time of scene i in the final (crossfaded) video."""
    print("📝 Generating word-synced captions...")
    events = []

    for idx, scene in enumerate(script_data.get("scenes", [])):
        offset = timeline_offsets[idx]
        words = scene.get("words") or []

        if words:
            chunks = _chunk_words(words)
            for c, chunk in enumerate(chunks):
                start = offset + chunk[0]["start"]
                # hold each caption until the next chunk starts (no dead gaps)
                if c + 1 < len(chunks):
                    end = offset + chunks[c + 1][0]["start"]
                else:
                    end = offset + scene["duration"]
                text = " ".join(w["word"] for w in chunk).upper()
                text = text.replace("{", "").replace("}", "")
                # pop-in: scale 70% -> 100% over 90ms
                fx = r"{\fscx70\fscy70\t(0,90,\fscx100\fscy100)}"
                events.append(
                    f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Word,,0,0,0,,{fx}{text}"
                )
        else:
            # fallback (gTTS scene, no timings): show the sentence in halves
            dur = scene["duration"]
            sentence = scene.get("narration", "").upper()
            half = len(sentence) // 2
            split = sentence.rfind(" ", 0, half)
            parts = [sentence[:split], sentence[split + 1:]] if split > 0 else [sentence]
            step = dur / len(parts)
            for p, part in enumerate(parts):
                events.append(
                    f"Dialogue: 0,{_ass_time(offset + p * step)},"
                    f"{_ass_time(offset + (p + 1) * step)},Word,,0,0,0,,{part}"
                )

    with open(output_ass, "w", encoding="utf-8") as f:
        f.write(ASS_HEADER + "\n".join(events))
    print(f"✅ Captions saved: {len(events)} caption events")


def _ken_burns_filter(duration: float, style: int) -> str:
    """Four motion styles rotated across scenes. Source is upscaled 4x first so
    zoompan's integer sampling doesn't produce the classic jitter."""
    frames = max(int(duration * FPS), 1)
    up = f"scale={W * 2}:{H * 2}:flags=lanczos"
    common = f"d={frames}:s={W}x{H}:fps={FPS}"
    z_rate = 0.20 / frames  # total 20% zoom across the clip

    if style == 0:  # slow zoom in, centered
        zp = (f"zoompan=z='min(zoom+{z_rate:.6f},1.2)'"
              f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':{common}")
    elif style == 1:  # zoom out
        zp = (f"zoompan=z='if(eq(on,1),1.2,max(zoom-{z_rate:.6f},1.0))'"
              f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':{common}")
    elif style == 2:  # pan right with slight zoom
        zp = (f"zoompan=z='1.15':x='(iw-iw/zoom)*on/{frames}'"
              f":y='ih/2-(ih/zoom/2)':{common}")
    else:  # pan left with slight zoom
        zp = (f"zoompan=z='1.15':x='(iw-iw/zoom)*(1-on/{frames})'"
              f":y='ih/2-(ih/zoom/2)':{common}")
    return f"{up},{zp}"


def create_scene_clip(image_path, audio_path, duration, output_path, style):
    vf = _ken_burns_filter(duration, style)
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-framerate", str(FPS), "-i", image_path,
        "-i", audio_path,
        "-vf", vf,
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
        "-pix_fmt", "yuv420p",
        "-t", f"{duration:.3f}",
        output_path,
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, check=True)


def _pick_music() -> str | None:
    if not os.path.isdir(MUSIC_DIR):
        return None
    tracks = [f for f in os.listdir(MUSIC_DIR)
              if f.lower().endswith((".mp3", ".wav", ".m4a", ".ogg"))]
    if not tracks:
        return None
    return os.path.join(MUSIC_DIR, sorted(tracks)[random.randrange(len(tracks))])


def generate_video_pipeline(script_data: dict, build_dir: str = "build") -> str:
    print("🎬 Initializing Video Engine...")
    scenes = [s for s in script_data.get("scenes", [])
              if s.get("image_path") and s.get("audio_path")]

    # ---- Phase 1: render individual scene clips with varied motion ----
    clips, durations = [], []
    for idx, scene in enumerate(scenes):
        clip_path = os.path.join(build_dir, f"clip_{idx}.mp4")
        print(f"🎞️ Rendering scene {idx} ({scene['duration']:.1f}s, motion {idx % 4})...")
        create_scene_clip(scene["image_path"], scene["audio_path"],
                          scene["duration"], clip_path, style=idx % 4)
        clips.append(clip_path)
        durations.append(scene["duration"])

    # ---- Phase 2: crossfade all clips (video + audio) in one pass ----
    print("🔗 Crossfading scenes...")
    merged = os.path.join(build_dir, "merged.mp4")
    n = len(clips)
    if n == 1:
        os.replace(clips[0], merged)
        offsets = [0.0]
        total = durations[0]
    else:
        inputs = []
        for c in clips:
            inputs += ["-i", c]
        fc, offsets, t = [], [0.0], 0.0
        for i in range(n - 1):
            t += durations[i] - XFADE
            offsets.append(t)
        vprev, aprev = "[0:v]", "[0:a]"
        for i in range(1, n):
            vout = f"[v{i}]" if i < n - 1 else "[vout]"
            aout = f"[a{i}]" if i < n - 1 else "[aout]"
            fc.append(f"{vprev}[{i}:v]xfade=transition=fade:duration={XFADE}"
                      f":offset={offsets[i]:.3f}{vout}")
            fc.append(f"{aprev}[{i}:a]acrossfade=d={XFADE}{aout}")
            vprev, aprev = vout, aout
        total = sum(durations) - XFADE * (n - 1)
        cmd = ["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(fc),
               "-map", "[vout]", "-map", "[aout]",
               "-c:v", "libx264", "-preset", "medium", "-crf", "20",
               "-c:a", "aac", "-b:a", "192k", "-pix_fmt", "yuv420p", merged]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, check=True)

    # ---- Phase 3: captions (timed against the crossfaded timeline) ----
    ass_path = os.path.join(build_dir, "subtitles.ass")
    generate_subtitles(script_data, ass_path, timeline_offsets=offsets)

    # ---- Phase 4: grade + captions + music + end fade, single final encode ----
    print("🔥 Final render: grade, captions, music...")
    final_output = os.path.join(build_dir, "final_short.mp4")
    safe_ass = os.path.abspath(ass_path).replace("\\", "/").replace(":", "\\:")
    safe_fonts = os.path.abspath(FONTS_DIR).replace("\\", "/").replace(":", "\\:")

    vf = (
        "vignette=PI/4.5,"                      # subtle dark corners
        "noise=alls=5:allf=t+u,"                # fine animated film grain
        "eq=contrast=1.05:saturation=0.92,"     # slight crush + desaturate
        f"ass='{safe_ass}':fontsdir='{safe_fonts}',"
        f"fade=t=out:st={max(total - 0.6, 0):.2f}:d=0.6"
    )

    music = _pick_music()
    if music:
        print(f"🎵 Mixing background music: {os.path.basename(music)}")
        af = (
            f"[1:a]aloop=loop=-1:size=2e9,atrim=0:{total:.3f},"
            f"volume=0.14,afade=t=out:st={max(total - 1.5, 0):.2f}:d=1.5[bg];"
            f"[0:a][bg]amix=inputs=2:duration=first:normalize=0,"
            f"afade=t=out:st={max(total - 0.8, 0):.2f}:d=0.8[aout]"
        )
        cmd = ["ffmpeg", "-y", "-i", merged, "-i", music,
               "-filter_complex", af, "-map", "0:v", "-map", "[aout]",
               "-vf", vf,
               "-c:v", "libx264", "-preset", "medium", "-crf", "19",
               "-c:a", "aac", "-b:a", "192k", final_output]
    else:
        print("🎵 No track in music/ — rendering without background music.")
        cmd = ["ffmpeg", "-y", "-i", merged, "-vf", vf,
               "-c:v", "libx264", "-preset", "medium", "-crf", "19",
               "-c:a", "copy", final_output]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, check=True)

    print(f"✅ Video pipeline complete: {final_output}")
    return final_output
