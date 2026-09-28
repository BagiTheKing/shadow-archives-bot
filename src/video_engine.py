"""
Video engine — the editing upgrade.

What changed vs the old version:
1. CAPTIONS: word-synced chunks (2-3 words) at the bottom third with a pop-in
   animation, instead of a full paragraph parked in the center of the screen.
2. MOTION: each scene gets a different Ken Burns move (zoom in / zoom out /
   pan left / pan right), with the source upscaled first to kill zoompan jitter.
   Scenes are cut into shots of ~3.5s (painting, archive picture, punch-in),
   see plan_shots.
3. TRANSITIONS: 0.4s crossfades between scenes instead of hard cuts.
4. GRADE: subtle vignette + film grain for a cohesive cinematic look.
5. MUSIC: if a track exists in music/, it's looped, ducked -20dB under the
   narration, and faded out at the end. Lowered from -17dB (0.14) after the
   owner tested a real render and called it too loud.
6. Output is true 1080x1920 30fps.
7. EMPHASIS: figures in the captions are drawn in the accent colour, and the
   ending's punch phrase slams on screen as it is spoken (punch_timing).
8. SOUND: a low impact under the hook's figure and under the punch phrase,
   and the music drawn nearly out for the story's last line (sound_cues,
   music_volume).
"""

import os
import random
import re
import subprocess

from caption_track import write_srt
from longform_video import _bump, _find_phrase_time, build_sfx_bed
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

# The ending's punch phrase (the writer's punch_phrase, verbatim from the last
# line): slammed on screen in the accent colour as the narrator says it, the
# same reveal the long-form videos use for theirs.
PUNCH_SIZE = 118
PUNCH_Y = 560                  # from the top of the frame
PUNCH_HOLD = 2.4               # at least this long on screen
PUNCH_TAIL = 0.8               # held this long after the phrase is spoken
PUNCH_RULE_WIDTH = 520
ACCENT_ASS = r"&H0000E8FF&"   # the channel's yellow, as the long-form uses
WHITE_ASS = r"&H00FFFFFF&"

# Figures are the point of this channel, so they read as the point: every
# number in a caption is drawn in the accent colour. Digits, and number words
# except "zero" and "one" ("no one" is not a figure).
_NUMBER_WORD = re.compile(
    r"^(two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|"
    r"twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|"
    r"hundreds?|thousands?|millions?|billions?|dozens?)(-\w+)?$")


def _is_figure(word: str) -> bool:
    bare = re.sub(r"[^\w-]", "", word.lower())
    return bool(re.search(r"\d", bare) or _NUMBER_WORD.match(bare))


def _caption_text(chunk) -> str:
    """One caption chunk, upper-cased, with its figures in the accent colour."""
    out = []
    for w in chunk:
        word = w["word"].upper().replace("{", "").replace("}", "")
        if _is_figure(w["word"]):
            word = f"{{\\c{ACCENT_ASS}}}{word}{{\\c{WHITE_ASS}}}"
        out.append(word)
    return " ".join(out)


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
Style: Punch,{FONT_NAME},{PUNCH_SIZE},&H0000E8FF,&H00FFFFFF,&H00000000,&HB0000000,-1,0,0,0,100,100,2,0,1,7,4,8,70,70,{PUNCH_Y},1
Style: Rule,{FONT_NAME},10,&H00000000,&H00000000,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
# Punch sits top-centre (Alignment=8) in the upper third, well clear of the
# narration captions at the bottom and of the Shorts title overlay.
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


def _last_story_index(scenes):
    story = [i for i, s in enumerate(scenes) if not s.get("is_outro")]
    return story[-1] if story else None


def punch_timing(script_data: dict, timeline_offsets: list):
    """(start, end) on the video's timeline where the punch phrase is spoken,
    or None when there is no phrase or its words cannot be found."""
    phrase = (script_data.get("punch_phrase") or "").strip()
    scenes = script_data.get("scenes") or []
    idx = _last_story_index(scenes)
    if not phrase or idx is None or idx >= len(timeline_offsets):
        return None
    try:
        found = _find_phrase_time(scenes[idx], phrase)
    except (KeyError, TypeError, ValueError):
        found = None
    if not found:
        print(f"   ⚠️ Punch phrase {phrase!r} not found in the word timings.")
        return None
    return timeline_offsets[idx] + found[0], timeline_offsets[idx] + found[1]


def last_line_start(script_data: dict, timeline_offsets: list):
    """When the story's last sentence begins on the video's timeline."""
    scenes = script_data.get("scenes") or []
    idx = _last_story_index(scenes)
    if idx is None or idx >= len(timeline_offsets):
        return None
    words = _repunctuate(scenes[idx].get("words") or [],
                         scenes[idx].get("narration", ""))
    if not words:
        return None
    start = 0
    for i, w in enumerate(words[:-1]):
        if w["word"].rstrip().endswith((".", "!", "?")):
            start = i + 1
    return timeline_offsets[idx] + float(words[start]["start"])


def hook_figure_time(script_data: dict, timeline_offsets: list):
    """When the hook's first figure is spoken, or None if it has none."""
    scenes = script_data.get("scenes") or []
    if not scenes or not timeline_offsets or scenes[0].get("is_outro"):
        return None
    for w in scenes[0].get("words") or []:
        if _is_figure(w.get("word", "")):
            return timeline_offsets[0] + float(w["start"])
    return None


def _punch_events(start: float, end_spoken: float, phrase: str):
    """The long-form punch reveal at Shorts size: small, blurred and clear,
    punching past full size and settling back in under 300ms, with an accent
    rule drawing out underneath."""
    end = start + max(PUNCH_HOLD, (end_spoken - start) + PUNCH_TAIL)
    fx = (r"{\alpha&HFF&\be8\fscx55\fscy55\frz-3"
          r"\t(0,180,\alpha&H00&\fscx116\fscy116\frz2\be0)"
          r"\t(180,260,\fscx100\fscy100\frz0)}")
    body = phrase.replace("{", "").replace("}", "").upper()
    text = f"Dialogue: 2,{_ass_time(start)},{_ass_time(end)},Punch,,0,0,0,,{fx}{body}"
    rule_x = W // 2 - PUNCH_RULE_WIDTH // 2
    rule_y = PUNCH_Y + PUNCH_SIZE + 24
    rule = (f"{{\\an7\\pos({rule_x},{rule_y})\\1c{ACCENT_ASS}\\1a&H30&"
            f"\\bord0\\shad0\\fscx0\\fad(0,300)\\t(60,260,\\fscx100)\\p1}}"
            f"m 0 0 l {PUNCH_RULE_WIDTH} 0 l {PUNCH_RULE_WIDTH} 7 l 0 7{{\\p0}}")
    rule_line = f"Dialogue: 1,{_ass_time(start)},{_ass_time(end)},Rule,,0,0,0,,{rule}"
    return [rule_line, text]


def generate_subtitles(script_data: dict, output_ass: str, timeline_offsets: list,
                       total_duration: float = 0.0, cta_text: str = None):
    """Builds word-synced pop-in captions. timeline_offsets[i] is the global
    start time of scene i in the final (crossfaded) video. total_duration is the
    length of the crossfaded timeline, used to place the end-card CTA.
    cta_text overrides CTA_TEXT; "" draws no end card (longform_clips.py
    draws its own)."""
    cta = CTA_TEXT if cta_text is None else cta_text
    print("📝 Generating word-synced captions...")
    events = []

    # While the punch phrase is on screen, the bottom captions saying the same
    # words stand down: the phrase twice at once is noise, not emphasis.
    punch = punch_timing(script_data, timeline_offsets)
    quiet = None
    if punch:
        punch_lines = _punch_events(
            punch[0], punch[1], script_data["punch_phrase"])
        events += punch_lines
        quiet = (punch[0] - 0.05, punch[1])
        print(f"💥 Punch phrase at {punch[0]:.1f}s: "
              f"{script_data['punch_phrase']!r}")

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
                if quiet and quiet[0] <= start < quiet[1]:
                    continue
                text = _caption_text(chunk)
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
    if cta and total_duration > CTA_DURATION and not has_card:
        cta_start = total_duration - CTA_DURATION
        # \fad(400,0) — fade in, then hold; the video's own 0.6s fade takes it out.
        fx = r"{\fad(400,0)}"
        events.append(
            f"Dialogue: 1,{_ass_time(cta_start)},{_ass_time(total_duration)},"
            f"Cta,,0,0,0,,{fx}{cta}"
        )
        print(f"📣 End-card CTA added at {cta_start:.1f}s: {cta!r}")

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


def _punch_filter(duration: float) -> str:
    """A tight crop of the same frame, drifting in slowly: the cut from a wide
    shot to this reads as a new shot without spending another generation.
    Aimed a little above centre, where the single figure's face sits."""
    frames = max(int(duration * FPS), 1)
    up = f"scale={W * 2}:{H * 2}:flags=lanczos"
    return (f"{up},zoompan=z='{PUNCH_ZOOM}+{0.06 / frames:.6f}*on'"
            f":x='iw/2-(iw/zoom/2)':y='(ih-ih/zoom)*0.42'"
            f":d={frames}:s={W}x{H}:fps={FPS}")


def _static_filter(duration: float) -> str:
    """No motion. The next-file card is typography, and Ken Burns on
    typography either crops the headline or drifts it off-centre — the whole
    point of the card is that the frame stops moving when the story ends."""
    return f"scale={W}:{H}:flags=lanczos,fps={FPS}"


# A still held for nine seconds is where a Short loses people: every scene was
# one image for its whole narration, about 9s each. Scenes are now cut into
# shots of about SHOT_SECONDS: the painting wide, a real archive picture when
# the scene has one (shorts_archive.py), and a punch-in on the painting. Cuts
# inside a scene are hard; scenes still dissolve into each other.
SHOT_SECONDS = float(os.getenv("SHORTS_SHOT_SECONDS", "3.5"))
MAX_SHOTS = 3
PUNCH = "punch"
PUNCH_ZOOM = 1.38
# Archive pictures sit over a blurred copy of themselves; a pan would slide
# the picture off centre, so they only zoom.
_ARCHIVE_MOTIONS = (0, 1)


def plan_shots(scene: dict, clip_len: float, idx: int):
    """[(image_path, motion)] for one scene: motion is a Ken Burns style
    (0-3), PUNCH, or None for the static next-file card."""
    image = scene["image_path"]
    if scene.get("is_outro"):
        return [(image, None)]
    archive = scene.get("archive_path")
    base = idx % 4
    if archive and scene.get("image_source") == "designed-fallback":
        # The generator failed this scene: the real picture takes its place.
        image, archive, base = archive, None, _ARCHIVE_MOTIONS[idx % 2]

    n = max(1, min(MAX_SHOTS, round(clip_len / SHOT_SECONDS)))
    if archive and n < 2 and clip_len >= 2.4:
        n = 2                     # a real picture is worth a quick cut
    if archive:
        pool = [(image, base), (archive, _ARCHIVE_MOTIONS[idx % 2]),
                (image, PUNCH)]
    else:
        pool = [(image, base), (image, PUNCH), (image, (base + 2) % 4)]
    return pool[:n]


def _shot_frames(clip_len: float, n: int):
    """Frames per shot, summing exactly to the clip's frame count."""
    total = max(int(round(clip_len * FPS)), n)
    each = total // n
    return [each] * (n - 1) + [total - each * (n - 1)]


def create_scene_clip(shots, audio_path, duration, output_path):
    """One scene, rendered XFADE seconds longer than its narration, as one or
    more shots cut together.

    The extra tail is silence, and it exists so the picture has something to
    dissolve across that is not speech. Consecutive clips overlap by exactly
    XFADE: the video crossfades over that overlap, the audio does not, and the
    narration of one scene therefore never plays under the narration of the
    next. That overlap was audible on every published Short — "Two thousand
    died because a harbour had..." still fading while the next line started.
    """
    clip_len = duration + XFADE
    frames = _shot_frames(clip_len, len(shots))
    inputs, graph = [], []
    for k, ((image, motion), n) in enumerate(zip(shots, frames)):
        inputs += ["-loop", "1", "-framerate", str(FPS), "-i", image]
        seconds = n / FPS
        if motion is None:
            vf = _static_filter(seconds)
        elif motion == PUNCH:
            vf = _punch_filter(seconds)
        else:
            vf = _ken_burns_filter(seconds, motion)
        graph.append(f"[{k}:v]{vf},trim=end_frame={n},setpts=PTS-STARTPTS,"
                     f"setsar=1[s{k}]")
    a = len(shots)
    graph.append("".join(f"[s{k}]" for k in range(a))
                 + f"concat=n={a}:v=1:a=0,format=yuv420p[v]")
    graph.append(f"[{a}:a]apad=pad_dur={XFADE:.3f}[a]")
    cmd = [
        "ffmpeg", "-y", *inputs,
        "-i", audio_path,
        "-filter_complex", ";".join(graph),
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
        "-pix_fmt", "yuv420p", "-r", str(FPS),
        "-t", f"{clip_len:.3f}",
        output_path,
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, check=True)


# Sound design, borrowed from the long-form lanes and held just as low: these
# are punctuation, and the moment a viewer notices them as effects the video
# reads as a template. Two impacts (under the hook's figure and under the
# punch phrase), and the music bed drawn nearly out of the way for the
# story's last line, so the ending lands on the voice alone.
MUSIC_LEVEL = 0.10
MUSIC_LAST_LINE = 0.25         # share of MUSIC_LEVEL left under the last line
DUCK_LEAD = 0.5                # the duck is complete this long before the line


def sound_cues(script_data: dict, offsets: list):
    """[("impact", t)] for the sfx bed."""
    cues = []
    hook = hook_figure_time(script_data, offsets)
    if hook is not None:
        cues.append(("impact", hook))
    punch = punch_timing(script_data, offsets)
    if punch:
        cues.append(("impact", punch[0]))
    return cues


def music_volume(script_data: dict, offsets: list, total: float) -> str:
    """The bed's level over time as an ffmpeg expression: MUSIC_LEVEL, dipping
    to MUSIC_LAST_LINE of it from just before the last line to the end of the
    story, and back up for the next-file outro."""
    start = last_line_start(script_data, offsets)
    scenes = script_data.get("scenes") or []
    idx = _last_story_index(scenes)
    if start is None or idx is None:
        return f"{MUSIC_LEVEL:.3f}"
    story_end = offsets[idx] + scenes[idx]["duration"]
    a, b = max(0.0, start - DUCK_LEAD - 0.3), min(total, story_end + 0.3)
    if b <= a:
        return f"{MUSIC_LEVEL:.3f}"
    print(f"🎚️  Music ducks under the last line at {start:.1f}s.")
    return (f"{MUSIC_LEVEL:.3f}*(1-{1 - MUSIC_LAST_LINE:.3f}*"
            f"{_bump(a, b, fade=0.3)})")


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
        shots = plan_shots(scene, scene["duration"] + XFADE, idx)
        described = ", ".join(
            "static card" if m is None else
            ("archive" if img == scene.get("archive_path") else "painting")
            + (" punch-in" if m == PUNCH else "")
            for img, m in shots)
        print(f"🎞️ Rendering scene {idx} ({scene['duration']:.1f}s, "
              f"{len(shots)} shot(s): {described})...")
        create_scene_clip(shots, scene["audio_path"], scene["duration"],
                          clip_path)
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

    sfx = build_sfx_bed(sound_cues(script_data, offsets), total,
                        os.path.join(build_dir, "sfx.wav"))

    music = _pick_music()
    # Most tracks need nothing here (YouTube Audio Library, "Attribution not
    # required"), but a Creative Commons track only stays license-compliant
    # if its exact credit text is in the description — see music_attribution.py.
    script_data["music_attribution"] = attribution_for(music) if music else ""
    if music:
        print(f"🎵 Mixing background music: {os.path.basename(music)}")
        level = music_volume(script_data, offsets, total)
        sfx_in = "[2:a]" if sfx else ""
        af = (
            f"[1:a]aloop=loop=-1:size=2e9,atrim=0:{total:.3f},"
            f"volume=eval=frame:volume='{level}',"
            f"afade=t=out:st={max(total - 1.5, 0):.2f}:d=1.5[bg];"
            f"[0:a][bg]{sfx_in}amix=inputs={3 if sfx else 2}:duration=first:"
            f"normalize=0,"
            f"afade=t=out:st={max(total - 0.8, 0):.2f}:d=0.8[aout]"
        )
        cmd = ["ffmpeg", "-y", "-i", merged, "-i", music,
               *(["-i", sfx] if sfx else []),
               "-filter_complex", af, "-map", "0:v", "-map", "[aout]",
               "-vf", vf,
               "-c:v", "libx264", "-preset", "medium", "-crf", "19",
               "-c:a", "aac", "-b:a", "192k", final_output]
    else:
        print("🎵 No track in music/ — rendering without background music.")
        if sfx:
            cmd = ["ffmpeg", "-y", "-i", merged, "-i", sfx,
                   "-filter_complex",
                   "[0:a][1:a]amix=inputs=2:duration=first:normalize=0[aout]",
                   "-map", "0:v", "-map", "[aout]", "-vf", vf,
                   "-c:v", "libx264", "-preset", "medium", "-crf", "19",
                   "-c:a", "aac", "-b:a", "192k", final_output]
        else:
            cmd = ["ffmpeg", "-y", "-i", merged, "-vf", vf,
                   "-c:v", "libx264", "-preset", "medium", "-crf", "19",
                   "-c:a", "copy", final_output]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, check=True)

    print(f"✅ Video pipeline complete: {final_output}")
    return final_output
