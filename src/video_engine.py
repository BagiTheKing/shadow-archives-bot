"""
Video engine — the editing upgrade.

What changed vs the old version:
1. CAPTIONS: word-synced chunks (2-3 words) at the bottom third with a pop-in
   animation, instead of a full paragraph parked in the center of the screen.
2. MOTION: each scene gets a different Ken Burns move (zoom in / zoom out /
   pan left / pan right), with the source upscaled first to kill zoompan jitter.
3. TRANSITIONS: 0.4s crossfades between scenes instead of hard cuts.
4. GRADE: subtle vignette + film grain for a cohesive cinematic look.
5. MUSIC: if a track exists in music/, it's looped, ducked -20dB under the
   narration, and faded out at the end. Lowered from -17dB (0.14) after the
   owner tested a real render and called it too loud.
6. Output is true 1080x1920 30fps.
"""

import os
import random
import subprocess

from caption_track import write_srt
from music_attribution import attribution_for

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
Style: Cta,{FONT_NAME},60,&H0000E8FF,&H00FFFFFF,&H00000000,&HA0000000,-1,0,0,0,100,100,2,0,1,5,3,2,60,60,880,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
# Alignment=2 -> bottom-center; MarginV=430 keeps text above the Shorts UI
# (like/comment buttons, title overlay) but well below screen center.
# The Cta style sits at MarginV=880, well clear of the narration captions, so the
# end card can appear over the final line without colliding with it.

# Why an on-screen CTA: 6,373 views produced 16 subscribers (~0.25%). The only
# ask was buried in the description, which Shorts viewers effectively never open.
# Spoken CTAs cost retention, so this is a silent overlay on the last few seconds.
# "FOR THE NEXT FILE", because the narrator is naming that next file out loud in
# the same seconds — the overlay and the spoken tease make one ask together.
CTA_TEXT = os.getenv("CTA_TEXT", "SUBSCRIBE FOR THE NEXT FILE")
# Long enough to sit under the spoken "Next file: ..." tease, which runs about
# four seconds at the end of the narration.
CTA_DURATION = 4.0  # seconds of screen time at the end of the video


def _ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int(round((seconds - int(seconds)) * 100))
    if cs == 100:
        s, cs = s + 1, 0
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _repunctuate(words, narration: str):
    """Puts the narration's punctuation back onto the word timings.

    edge-tts reports each WordBoundary as a bare token — "started", not
    "started." — so every caption chunk arrived stripped of punctuation and
    then got upper-cased. On screen that reads as one unbroken shout:
    "ADMIRAL VERNON MINTED VICTORY MEDALS BEFORE HIS INVASION EVEN STARTED IN
    1741 VERNON SAILED 186 BRITISH WARSHIPS" — no way to tell where one
    sentence ends and the next begins, which is exactly the "hard to follow"
    complaint. It also broke _chunk_words, whose clause detection tests for
    trailing punctuation that was never there, so chunks were cut purely on
    the three-word ceiling.

    The narration is the source of truth, and the tokens are in the same order,
    so zip them. Mismatched counts (a number read as several words, an
    expansion the voice made) fall back to the bare tokens rather than risk
    pairing the wrong punctuation onto the wrong word.
    """
    spoken = (narration or "").split()
    if len(spoken) != len(words):
        return words
    return [{**w, "word": s} for w, s in zip(words, spoken)]


def _chunk_words(words, max_words=4):
    """Group word timings into caption chunks, breaking early on punctuation so
    chunks follow the rhythm of the sentence.

    Four words rather than three: with punctuation restored the breaks now fall
    on clause ends, and a ceiling of three was cutting short phrases in half
    ("SIX HUNDRED CAVALRYMEN" / "CHARGED THE WRONG" / "VALLEY") faster than
    they could be read.
    """
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


def generate_subtitles(script_data: dict, output_ass: str, timeline_offsets: list,
                       total_duration: float = 0.0):
    """Builds word-synced pop-in captions. timeline_offsets[i] is the global
    start time of scene i in the final (crossfaded) video. total_duration is the
    length of the crossfaded timeline, used to place the end-card CTA."""
    print("📝 Generating word-synced captions...")
    events = []

    for idx, scene in enumerate(script_data.get("scenes", [])):
        offset = timeline_offsets[idx]
        words = scene.get("words") or []

        # The next-file card already carries the tease as set type. Burning the
        # word-synced captions over it too would stack four things on one frame
        # — headline, tease, captions, subscribe overlay — which is exactly the
        # unreadable ending this card exists to fix.
        if scene.get("is_outro"):
            continue

        if words:
            chunks = _chunk_words(_repunctuate(words, scene.get("narration", "")))
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
            # last-resort: every engine now returns estimated timings even
            # without real ones (see audio_engine._estimate_word_timings), so
            # this only fires if a scene somehow has an empty words list —
            # show the sentence in halves rather than nothing at all
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

    # End card: fades in over the last few seconds, above the narration captions.
    #
    # Skipped when the video ends on a next-file card, which draws the same ask
    # into the image itself. This overlay is positioned from the bottom of the
    # frame and landed directly on the card's badge; the card can place it
    # against the type above it instead.
    has_card = any(s.get("is_outro")
                   for s in script_data.get("scenes", []))
    if CTA_TEXT and total_duration > CTA_DURATION and not has_card:
        cta_start = total_duration - CTA_DURATION
        # \fad(400,0) — fade in, then hold; the video's own 0.6s fade takes it out.
        fx = r"{\fad(400,0)}"
        events.append(
            f"Dialogue: 1,{_ass_time(cta_start)},{_ass_time(total_duration)},"
            f"Cta,,0,0,0,,{fx}{CTA_TEXT}"
        )
        print(f"📣 End-card CTA added at {cta_start:.1f}s: {CTA_TEXT!r}")

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


def _static_filter(duration: float) -> str:
    """No motion. The next-file card is typography, and Ken Burns on
    typography either crops the headline or drifts it off-centre — the whole
    point of the card is that the frame stops moving when the story ends."""
    return f"scale={W}:{H}:flags=lanczos,fps={FPS}"


def create_scene_clip(image_path, audio_path, duration, output_path, style):
    """One scene, rendered XFADE seconds longer than its narration.

    The extra tail is silence, and it exists so the picture has something to
    dissolve across that is not speech. Consecutive clips overlap by exactly
    XFADE: the video crossfades over that overlap, the audio does not, and the
    narration of one scene therefore never plays under the narration of the
    next. That overlap was audible on every published Short — "Two thousand
    died because a harbour had..." still fading while the next line started.
    """
    clip_len = duration + XFADE
    vf = (_static_filter(clip_len) if style is None
          else _ken_burns_filter(clip_len, style))
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-framerate", str(FPS), "-i", image_path,
        "-i", audio_path,
        "-vf", vf,
        "-af", f"apad=pad_dur={XFADE:.3f}",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
        "-pix_fmt", "yuv420p",
        "-t", f"{clip_len:.3f}",
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
        style = None if scene.get("is_outro") else idx % 4
        print(f"🎞️ Rendering scene {idx} ({scene['duration']:.1f}s, "
              f"motion {'static card' if style is None else style})...")
        create_scene_clip(scene["image_path"], scene["audio_path"],
                          scene["duration"], clip_path, style=style)
        clips.append(clip_path)
        durations.append(scene["duration"])

    # ---- Phase 2: crossfade all clips (video + audio) in one pass ----
    print("🔗 Crossfading scenes...")
    merged = os.path.join(build_dir, "merged.mp4")
    n = len(clips)
    if n == 1:
        os.replace(clips[0], merged)
        offsets = [0.0]
        # The clip carries the same XFADE of padded silence as any other, so
        # the length has to account for it or the end card lands early.
        total = durations[0] + XFADE
    else:
        inputs = []
        for c in clips:
            inputs += ["-i", c]

        # Each clip is XFADE longer than its narration, so consecutive clips
        # overlap by exactly XFADE and scene i starts when scene i-1 stopped
        # speaking. offsets are therefore the cumulative narration lengths —
        # which is also exactly where each scene's captions belong.
        fc, offsets, t = [], [0.0], 0.0
        for i in range(n - 1):
            t += durations[i]
            offsets.append(t)

        vprev = "[0:v]"
        for i in range(1, n):
            vout = f"[v{i}]" if i < n - 1 else "[vout]"
            fc.append(f"{vprev}[{i}:v]xfade=transition=fade:duration={XFADE}"
                      f":offset={offsets[i]:.3f}{vout}")
            vprev = vout

        # Summed, not cross-faded. Each track is delayed to the moment its own
        # scene begins, so the tails overlap only with the following scene's
        # padded silence. acrossfade here was mixing the end of one sentence
        # into the start of the next, which is what made the edit sound broken.
        for i in range(n):
            ms = int(round(offsets[i] * 1000))
            fc.append(f"[{i}:a]adelay={ms}|{ms}[d{i}]")
        fc.append("".join(f"[d{i}]" for i in range(n))
                  + f"amix=inputs={n}:duration=longest:normalize=0[aout]")

        total = sum(durations) + XFADE
        cmd = ["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(fc),
               "-map", "[vout]", "-map", "[aout]",
               "-c:v", "libx264", "-preset", "medium", "-crf", "20",
               "-c:a", "aac", "-b:a", "192k", "-pix_fmt", "yuv420p", merged]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, check=True)

    # ---- Phase 3: captions (timed against the crossfaded timeline) ----
    ass_path = os.path.join(build_dir, "subtitles.ass")
    generate_subtitles(script_data, ass_path, timeline_offsets=offsets,
                       total_duration=total)

    # The same timings, written out as a real caption track. The ASS above ends
    # up as pixels; this is the copy YouTube can actually read.
    write_srt(scenes, offsets, build_dir)

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
    # Most tracks need nothing here (YouTube Audio Library, "Attribution not
    # required"), but a Creative Commons track only stays license-compliant
    # if its exact credit text is in the description — see music_attribution.py.
    script_data["music_attribution"] = attribution_for(music) if music else ""
    if music:
        print(f"🎵 Mixing background music: {os.path.basename(music)}")
        af = (
            f"[1:a]aloop=loop=-1:size=2e9,atrim=0:{total:.3f},"
            f"volume=0.10,afade=t=out:st={max(total - 1.5, 0):.2f}:d=1.5[bg];"
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
