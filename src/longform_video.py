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

Motion design, after the "bad PowerPoint" review:
- Scenes may carry several images (image_paths) and are cut internally, so no
  still outlives MAX_SHOT_SECONDS regardless of how the narration was segmented.
- Scenes may carry a video_path: real muted b-roll, cover-cropped to frame.
- Chapter titles are typeset over the opening footage of each chapter (kicker +
  heading, slide-and-fade) instead of pausing the film for a title card.
- The stat callout is ONE ASS event (value \\N label) so the label can never
  collide with a wrapping value — which is exactly what it used to do.
- The final pass adds animated film grain; static frames read as alive.
"""

import os
import random
import re
import subprocess

from caption_track import write_srt
from music_attribution import attribution_for

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
Style: Chapter,{FONT_NAME},110,&H00FFFFFF,&H0000E8FF,&H00000000,&HB0000000,0,0,0,0,100,100,1,0,1,4,3,4,150,150,60,1
Style: Cta,{FONT_NAME},64,&H0000E8FF,&H00FFFFFF,&H00000000,&HB0000000,0,0,0,0,100,100,2,0,1,4,3,2,140,140,200,1
Style: Pivot,{FONT_NAME},128,&H00FFFFFF,&H0000E8FF,&H00000000,&HB0000000,0,0,0,0,100,100,2,0,1,5,4,5,120,120,60,1
Style: Scrim,{FONT_NAME},10,&H00000000,&H00000000,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1

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

# The long-form videos have never had an outro of any kind — they reach the last
# sentence and stop. The Shorts at least carry an end card, which is why the two
# formats convert so differently: 75 long-form views have produced nothing.
# Held longer than the Shorts version because a viewer seven minutes deep is not
# thumb-scrolling, and it sits above the narration captions rather than over them.
CTA_TEXT = os.getenv("LONGFORM_CTA_TEXT", "SUBSCRIBE — A NEW CATASTROPHE EVERY WEEK")
CTA_DURATION = 5.0


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


# On-screen image-source credit, bottom-left, light grey — the same credit
# text that already goes into the description's "Archival imagery and
# footage" block (build_credit_block in longform_main.py), now also burned
# into the frame it names. Small and corner-anchored on purpose: this is a
# citation, not a graphic moment, so it gets none of the stat callout's
# fanfare — just an outline for legibility over an unpredictable photo, the
# same treatment the narration captions already use.
CREDIT_FONT_SIZE = 30
CREDIT_MARGIN_X, CREDIT_MARGIN_Y = 24, 22
CREDIT_MAX_CHARS = 70
CREDIT_MIN_SECONDS = 0.4  # shorter than this and it would only flash


def _shorten_credit(text: str) -> str:
    text = text.strip()
    return text if len(text) <= CREDIT_MAX_CHARS else text[:CREDIT_MAX_CHARS - 1].rstrip() + "…"


def _credit_events(scene: dict, offset: float):
    """One event per real photo/footage credit this scene actually has,
    timed to roughly track which shot is on screen. A scene with two real
    photos and one generated fill-in (longform_visual fills gaps with AI
    when a query comes up short) only gets two credit events, aligned to
    the two real shots — the generated one gets none, because there is
    nothing to credit.

    credits[i] corresponds to image_paths[i] by construction (see
    longform_visual.generate_longform_visuals: real images and their
    credits are appended together, generated fill-ins after and uncredited),
    so no separate mapping is needed beyond that shared index.
    """
    credits = scene.get("credit") or []
    if not credits:
        return []
    images = scene.get("image_paths") or [scene.get("image_path")]
    n = max(1, len(images))
    duration = float(scene.get("duration") or 0.0)
    starts = _shot_starts(duration, n)

    events = []
    for i, credit in enumerate(credits):
        if i >= len(starts):
            break
        start = offset + max(0.0, starts[i])
        end = offset + (starts[i + 1] if i + 1 < len(starts) else duration)
        if end - start < CREDIT_MIN_SECONDS:
            continue
        text = _shorten_credit(credit).replace("{", "").replace("}", "")
        fx = (f"{{\\an1\\pos({CREDIT_MARGIN_X},{H - CREDIT_MARGIN_Y})\\fad(200,200)"
              f"\\fs{CREDIT_FONT_SIZE}\\c&HD8D8D8&\\bord2\\shad1}}")
        events.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Body,,0,0,0,,{fx}{text}")
    return events


_PART_WORDS = ["ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT"]


# Where the accent rule sits beneath the stat block, and how wide it draws
# in. Hand-placed against the Stat style's own metrics (170pt value line +
# 46pt label line starting at MarginV=120) and confirmed against a real
# rendered frame — see the comment on _stat_event.
STAT_RULE_Y = 405
STAT_RULE_WIDTH = 460


def _stat_event(start: float, value: str, label: str):
    """The number callout: value + forced line break + label, as a SINGLE
    event so long values can never wrap onto the label (see \\q2 below), plus
    a second event for the accent rule beneath it. Returns [rule, text] —
    two Dialogue lines, since ASS has no way to layer two effects in one.

    The old animation was a flat fade-and-grow, which the owner called out
    on a real render as too plain for what should be the video's biggest
    graphic beat. This is a fast "detail snapping into focus" reveal
    instead: the value starts small, blurred and transparent, sharpens and
    punches past full size, then settles back — under 300ms total, so it
    reads as impact rather than a wobble, and still holds clearly for the
    remaining ~3.2s of STAT_HOLD. \\be (edge blur) and a couple of degrees
    of \\frz rotation do the "coming into focus" and "landing" work; there
    is no colour flash or spin, which would read as a lower-tier social
    template rather than this channel's restrained, forensic look.

    The rule draws in from its left edge over the same window (reusing the
    \\p1 vector-rectangle technique _pivot_events already uses for its own
    accent rule), so the video's two "big graphic moment" beats — the
    pivot card and this — share one visual language instead of two.
    """
    end = start + STAT_HOLD
    # Two non-overlapping \t() stages per tag — libass's handling of two
    # \t() blocks racing the SAME tag over overlapping windows is undefined,
    # which an earlier draft of this did with \be (blur cleared by both a
    # 0-90ms and a 0-180ms block at once). Stage 1 (0-180ms) resolves alpha,
    # blur and the overshoot scale/rotation together; stage 2 (180-260ms)
    # settles scale and rotation back to rest.
    punch = (
        r"{\an8\q2\alpha&HFF&\be8\fscx55\fscy55\frz-3"
        r"\t(0,180,\alpha&H00&\fscx116\fscy116\frz2\be0)"
        r"\t(180,260,\fscx100\fscy100\frz0)}"
    )
    body = value.replace("{", "").replace("}", "")
    text = f"{punch}{body}"
    if label:
        clean = label.replace("{", "").replace("}", "").upper()
        text += r"\N{\fs46\c&HE8E8E8&\bord3}" + clean
    stat_line = f"Dialogue: 1,{_ass_time(start)},{_ass_time(end)},Stat,,0,0,0,,{text}"

    rule_x = W // 2 - STAT_RULE_WIDTH // 2
    rule = (
        f"{{\\an4\\pos({rule_x},{STAT_RULE_Y})\\1c{ACCENT_ASS}\\1a&H30&"
        f"\\bord0\\shad0\\fscx0\\fad(0,300)\\t(60,260,\\fscx100)\\p1}}"
        f"m 0 0 l {STAT_RULE_WIDTH} 0 l {STAT_RULE_WIDTH} 5 l 0 5{{\\p0}}"
    )
    rule_line = f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Scrim,,0,0,0,,{rule}"
    return [rule_line, stat_line]


def _find_stat_value_time(group, offs, value):
    """Chapter-local second at which this chapter's key_stat value is
    actually spoken, found from the scene word timings (real or estimated —
    see audio_engine.py's _estimate_word_timings) rather than guessed from
    chapter position. The old placement ("1s after the first scene, or 35%
    into the chapter") had no relation to the narration at all — the owner's
    complaint on a real render was exactly this: a number appearing on
    screen with no sync to when it was actually said.

    Returns None when no scene's words literally contain the value (the
    script phrased it differently, e.g. narrating "thirteen thousand" for a
    key_stat of "13,000"), so the caller can fall back to the old
    percentage-of-chapter placement rather than show nothing at all.
    """
    target = str(value).strip()
    target_digits = re.sub(r"\D", "", target)
    for scene, local_start in zip(group, offs):
        for w in scene.get("words") or []:
            token = (w.get("word") or "").strip().strip("\"'.,!?;:—")
            if token == target or (target_digits and
                                   re.sub(r"\D", "", token) == target_digits):
                return local_start + float(w.get("start", 0.0))
    return None


def _chapter_title_event(start: float, duration: float, number: int,
                         heading: str) -> str:
    """Documentary-style chapter title over the footage: small accent kicker,
    big heading, sliding in from the left and fading out. Replaces the old
    full-screen title cards."""
    hold = min(4.2, max(2.0, duration - 0.4))
    kicker = f"PART {_PART_WORDS[(number - 1) % len(_PART_WORDS)]}"
    y = int(H * 0.52)
    fx = (f"{{\\an4\\move(110,{y},150,{y},0,420)\\fad(280,420)\\q2}}"
          f"{{\\fs44\\c{ACCENT_ASS}\\bord2}}{kicker}"
          r"\N{\fs104\c&HFFFFFF&\bord4}" + heading.upper())
    return (f"Dialogue: 1,{_ass_time(start)},{_ass_time(start + hold)},"
            f"Chapter,,0,0,0,,{fx}")


# ---------------------------------------------------------------------------
# The midpoint reset
#
# Retention graphs slump in the middle, and this one has nothing in the middle
# to slump against: chapter three looked, sounded and cut exactly like chapter
# two. The arc already knows which chapter is its turning point — "The
# Decision" is the chapter the whole format is built around — and it was being
# rendered with the same sliding corner label as every other chapter.
#
# So the pivot announces itself. The frame dims, the label moves to the centre
# and doubles in size, an accent rule draws under it, the narration has just
# taken a beat of silence, and the music ducks and comes back up. It costs
# about three seconds and it is the only moment in seven minutes that looks
# like nothing else in the video.
# ---------------------------------------------------------------------------

# How long the pivot card holds. Longer than a normal chapter label: it is not
# orientation, it is a gear change, and it needs to be felt.
PIVOT_HOLD = 3.4

# How dark the frame goes behind it. &H00 is opaque, &HFF is invisible.
PIVOT_SCRIM_ALPHA = "&H55&"


def _pivot_events(start: float, duration: float, number: int, heading: str):
    """The midpoint card: a dimmed frame, a centred heading, an accent rule.

    Returned as three events rather than one because they layer — the scrim has
    to sit under the type, and the rule has to sit under both.
    """
    hold = min(PIVOT_HOLD, max(1.8, duration - 0.4))
    end = start + hold
    fade = r"\fad(420,520)"
    cx, cy = W // 2, int(H * 0.5)

    # A full-frame wash, drawn rather than styled: libass has no box primitive.
    scrim = (f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Scrim,,0,0,0,,"
             f"{{\\an7\\pos(0,0)\\1c&H000000&\\1a{PIVOT_SCRIM_ALPHA}"
             f"\\bord0\\shad0{fade}\\p1}}"
             f"m 0 0 l {W} 0 l {W} {H} l 0 {H}{{\\p0}}")

    kicker = f"PART {_PART_WORDS[(number - 1) % len(_PART_WORDS)]}"
    text = (f"{{\\an5\\pos({cx},{cy}){fade}\\q2}}"
            f"{{\\fs54\\c{ACCENT_ASS}\\bord2}}{kicker}"
            r"\N{\fs128\c&HFFFFFF&\bord5}" + heading.upper())
    card = (f"Dialogue: 2,{_ass_time(start)},{_ass_time(end)},Pivot,,0,0,0,,"
            f"{text}")

    # The accent rule, drawn under the heading.
    rule_y = cy + 120
    rule = (f"Dialogue: 1,{_ass_time(start)},{_ass_time(end)},Scrim,,0,0,0,,"
            f"{{\\an5\\pos({cx},{rule_y})\\1c{ACCENT_ASS}\\1a&H00&"
            f"\\bord0\\shad0{fade}\\p1}}"
            f"m 0 0 l 360 0 l 360 6 l 0 6{{\\p0}}")

    return [scrim, rule, card]


def generate_subtitles(scenes, output_ass, offsets, stat_cues=None,
                       chapter_titles=None, total_duration=0.0):
    """offsets[i] is the global start time of scene i on the final timeline.
    stat_cues is [(start_seconds, value, label), ...] for the number callouts.
    chapter_titles is [(start_seconds, scene_duration, number, heading), ...].
    total_duration places the end card, and is 0 when there is no room for one."""
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

        events.extend(_credit_events(scene, offset))

    # Number callouts, top of frame. _stat_event returns [rule, text] — two
    # events, since the accent rule and the value/label are separate layers.
    for start, value, label in (stat_cues or []):
        events.extend(_stat_event(start, str(value), str(label or "")))

    for cue in (chapter_titles or []):
        # (start, duration, number, heading[, is_pivot])
        start, duration, number, heading = cue[:4]
        is_pivot = bool(cue[4]) if len(cue) > 4 else False
        if is_pivot:
            events.extend(_pivot_events(start, duration, number, heading))
        else:
            events.append(_chapter_title_event(start, duration, number, heading))

    # End card over the closing seconds. Layer 1 keeps it above the narration
    # captions, and its own margin keeps it clear of them.
    if CTA_TEXT and total_duration > CTA_DURATION:
        cta_start = total_duration - CTA_DURATION
        events.append(
            f"Dialogue: 1,{_ass_time(cta_start)},{_ass_time(total_duration)},"
            f"Cta,,0,0,0,,{{\\fad(500,0)}}{CTA_TEXT}")
        print(f"📣 End card at {cta_start:.1f}s: {CTA_TEXT!r}")

    with open(output_ass, "w", encoding="utf-8") as f:
        f.write(ASS_HEADER + "\n".join(events))
    print(f"✅ Captions: {len(events)} events "
          f"({len(stat_cues or [])} number callouts, "
          f"{len(chapter_titles or [])} chapter titles)")


# A real Trending run's owner called the motion too "classic" — textbook
# Ken Burns is dead smooth by design, which is exactly what reads as inert
# next to the genre's own successful channels (Fascinating Horror, Plainly
# Difficult), whose archival footage is never perfectly stable. This is a
# small, continuous handheld-style drift layered onto every path below, not
# a shake-cam effect: JITTER_AMOUNT is a fraction of the frame, and at the
# zoom levels here (1.0-1.20) there is 12-20% of headroom between the crop
# window and the source edge, so this jitter — under 1% — never risks
# pushing the window past it even at a pan's extreme; clip() in _jitter
# guards that regardless.
JITTER_AMOUNT = 0.006


def _jitter(expr: str, axis_max: str, style: int, axis: str) -> str:
    """Wraps a base zoompan x/y path expression with a small sinusoidal
    drift, clamped back into the valid crop range. Period lengths (in
    frames) are derived from `style` and differ between x and y so the
    drift traces a loose, organic path rather than a perfect circle, and
    differ scene to scene so neighbouring clips don't shake in lockstep."""
    period = (55 if axis == "x" else 71) + (style * (17 if axis == "x" else 23)) % 40
    phase = 0 if axis == "x" else 1.3
    dim = "iw" if axis == "x" else "ih"
    jitter = f"{dim}*{JITTER_AMOUNT}*sin(2*PI*on/{period}+{phase})"
    return f"clip({expr}+{jitter},0,{axis_max})"


def _ken_burns(duration: float, style: int) -> str:
    """Eight motions instead of four, including diagonals and vertical
    drifts, each with a small handheld drift layered on top (see _jitter).
    No scale= prefix: longform_visual already saved the frame upscaled."""
    frames = max(int(duration * FPS), 1)
    common = f"d={frames}:s={W}x{H}:fps={FPS}"
    rate = 0.18 / frames
    cx, cy = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    # Horizontal / vertical travel expressions for a held zoom.
    right, left = f"(iw-iw/zoom)*on/{frames}", f"(iw-iw/zoom)*(1-on/{frames})"
    down, up = f"(ih-ih/zoom)*on/{frames}", f"(ih-ih/zoom)*(1-on/{frames})"
    x_max, y_max = "iw-iw/zoom", "ih-ih/zoom"

    raw_style, style = style, style % 8
    xy = {0: (cx, cy), 1: (cx, cy), 2: (right, cy), 3: (left, cy),
          4: (cx, down), 5: (cx, up), 6: (right, down), 7: (left, up)}
    x_expr, y_expr = xy[style]
    x = _jitter(x_expr, x_max, raw_style, "x")
    y = _jitter(y_expr, y_max, raw_style, "y")

    if style == 0:   # slow push in
        z = f"min(zoom+{rate:.6f},1.18)"
    elif style == 1:  # pull out
        z = f"if(eq(on,1),1.18,max(zoom-{rate:.6f},1.0))"
    elif style in (2, 3, 4, 5):  # pan/drift with a slight held zoom
        z = "1.12"
    elif style == 6:  # diagonal, top-left to bottom-right, pushing in
        z = f"min(zoom+{rate:.6f},1.20)"
    else:             # diagonal, bottom-right to top-left, pulling out
        z = f"if(eq(on,1),1.20,max(zoom-{rate:.6f},1.0))"
    return f"zoompan=z='{z}':x='{x}':y='{y}':{common}"


def _run(cmd):
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, check=True)


# Crossfade between shots INSIDE a scene — quicker than the between-scene 0.5s,
# because these cuts happen mid-sentence.
INNER_XFADE = 0.4


def _shot_durations(duration: float, n: int, overlap: float = INNER_XFADE):
    """Play length for each of n shots crossfading inside one scene. Overlaps
    are shared between neighbours, so each shot runs slightly longer than
    duration/n and the assembled scene still sums to exactly `duration`."""
    if n <= 1:
        return [duration]
    each = (duration + overlap * (n - 1)) / n
    return [each] * n


def _shot_starts(duration: float, n: int, overlap: float = INNER_XFADE):
    """Local start second of each of n shots inside one scene — the same
    cumulative crossfade math _scene_clip uses to place its xfade offsets,
    exposed here for anything else (like _credit_events) that needs to know
    which shot is actually on screen at a given moment."""
    if n <= 1:
        return [0.0]
    durs = _shot_durations(duration, n, overlap)
    starts, t = [0.0], 0.0
    for i in range(1, n):
        t += durs[i - 1] - overlap
        starts.append(t)
    return starts


# ---------------------------------------------------------------------------
# Why every clip runs XFADE longer than its narration
#
# The picture dissolves between scenes; the narration must not. Those are two
# different requirements and they used to be served by one number: the clips
# overlapped by XFADE and BOTH streams were crossfaded across that overlap.
#
# That only ever worked by accident. edge-tts leaves about a fifth of a second
# of silence at each end of every clip, so the overlap was mostly silence
# meeting silence and nobody heard the seam. The moment the dead-air trim
# removed that padding, the same crossfade started mixing the last half second
# of one sentence into the first half second of the next — two voices at once,
# forty-five times in six minutes. It sounds exactly like what it is: a badly
# cut edit.
#
# So the two are now separate. Each clip carries XFADE seconds of extra picture
# and XFADE seconds of silence after its narration ends. The video still
# dissolves across that overlap. The audio does not cross-fade at all: each
# scene's speech is laid down at the exact moment the previous scene's speech
# ended, and what overlaps the incoming line is the outgoing clip's silence.
# Speech is continuous and untouched; only the image dissolves.
# ---------------------------------------------------------------------------

def _scene_clip(scene, out, style):
    """One scene -> one clip, XFADE longer than its narration. Three shapes:
    - video_path: real muted b-roll, cover-cropped, looped to the narration.
    - one image: classic Ken Burns.
    - several images: Ken Burns per shot, crossfaded inside the scene, so a
      long narration segment no longer means a frozen frame."""
    audio, duration = scene["audio_path"], scene["duration"]
    clip_len = duration + XFADE
    # The picture runs on past the last word; the audio is padded to match so
    # the muxed clip has one length and the dissolve has something to dissolve.
    tail = ["-af", f"apad=pad_dur={XFADE:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2",
            "-pix_fmt", "yuv420p", "-t", f"{clip_len:.3f}", out]

    if scene.get("video_path"):
        vf = (f"scale={W}:{H}:force_original_aspect_ratio=increase,"
              f"crop={W}:{H},setsar=1,fps={FPS}")
        _run(["ffmpeg", "-y", "-stream_loop", "-1", "-i", scene["video_path"],
              "-i", audio, "-map", "0:v", "-map", "1:a", "-vf", vf, *tail])
        return

    images = scene.get("image_paths") or [scene["image_path"]]
    if len(images) == 1:
        _run(["ffmpeg", "-y", "-loop", "1", "-framerate", str(FPS),
              "-i", images[0], "-i", audio,
              "-vf", _ken_burns(clip_len, style), *tail])
        return

    durs = _shot_durations(clip_len, len(images))
    inputs = []
    for img, d in zip(images, durs):
        inputs += ["-loop", "1", "-framerate", str(FPS),
                   "-t", f"{d + 0.1:.3f}", "-i", img]
    inputs += ["-i", audio]

    fc = [f"[{i}:v]{_ken_burns(d, style + i * 3)}[z{i}]"
          for i, d in enumerate(durs)]
    starts = _shot_starts(clip_len, len(images))
    prev = "[z0]"
    for i in range(1, len(images)):
        vout = f"[m{i}]" if i < len(images) - 1 else "[vout]"
        # Same curated pool the between-scene cuts already rotate through
        # (see TRANSITIONS) rather than a flat "fade" every time — these
        # inner cuts happen mid-sentence inside one long scene, so a plain
        # dissolve every single time was the other place the video still
        # read as a slideshow rather than an edit.
        trans = TRANSITIONS[(style + i) % len(TRANSITIONS)]
        fc.append(f"{prev}[z{i}]xfade=transition={trans}:duration={INNER_XFADE}"
                  f":offset={starts[i]:.3f}{vout}")
        prev = vout
    _run(["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(fc),
          "-map", "[vout]", "-map", f"{len(images)}:a", *tail])


def _xfade_chapter(clips, durations, out, cut_index=0):
    """Assembles one chapter. The picture crossfades, the narration does not.

    `durations` are clip lengths, each already XFADE longer than its narration
    (see _scene_clip). So consecutive clips overlap by exactly XFADE, and a
    scene's speech begins the instant the previous scene's speech ended.

    The video is crossfaded, rotating transition style per cut; cut_index
    continues across chapters so the pattern does not restart every chapter.
    The audio is NOT crossfaded — each clip's track is delayed to its own start
    and the tracks are summed. What overlaps an incoming line is the outgoing
    clip's trailing silence, so nothing is ever mixed into speech.

    Returns (within-chapter offsets, total).
    """
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

    n = len(clips)
    fc, vprev = [], "[0:v]"
    for i in range(1, n):
        vout = f"[v{i}]" if i < n - 1 else "[vout]"
        trans = TRANSITIONS[(cut_index + i - 1) % len(TRANSITIONS)]
        fc.append(f"{vprev}[{i}:v]xfade=transition={trans}:duration={XFADE}"
                  f":offset={offsets[i]:.3f}{vout}")
        vprev = vout

    # Summed, not cross-faded. adelay places each track at the exact moment its
    # narration is due; normalize=0 keeps the level of a lone speaking track
    # untouched instead of dividing it by the number of inputs.
    for i, offset in enumerate(offsets):
        ms = int(round(offset * 1000))
        fc.append(f"[{i}:a]adelay={ms}|{ms}[d{i}]")
    fc.append("".join(f"[d{i}]" for i in range(n)) +
              f"amix=inputs={n}:duration=longest:normalize=0[aout]")

    _run(["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(fc),
          "-map", "[vout]", "-map", "[aout]",
          "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
          "-c:a", "aac", "-b:a", "160k", "-pix_fmt", "yuv420p", out])
    return offsets, sum(durations) - XFADE * (n - 1)


def _music_tracks():
    """Every usable track in the music directory, in a stable order."""
    if not os.path.isdir(MUSIC_DIR):
        return []
    return [os.path.join(MUSIC_DIR, f) for f in sorted(os.listdir(MUSIC_DIR))
            if f.lower().endswith((".mp3", ".wav", ".m4a", ".ogg"))]


def _pick_music():
    tracks = _music_tracks()
    return tracks[random.randrange(len(tracks))] if tracks else None


# How long one track takes to become the next. Long enough to read as a
# transition rather than a skip, short enough not to muddy both.
SCORE_CROSSFADE = 2.5


# A fixed, chapter-numbered score rather than the old free-floating 3-act
# system: the owner picked these five Kevin MacLeod (CC BY 4.0) tracks
# specifically for the shape of the arc — one per chapter across the
# opening, one for the close — and everything else dropped into music/
# that is not one of these five is the pool for whatever chapters sit
# between them. See plan_chapter_score.
MUSIC_OPENING_TRACKS = [
    "Crossing the Threshold - Ghostpocalypse - Kevin MacLeod.mp3",
    "A Singular Perversion - Darkness - Kevin MacLeod.mp3",
    "Epilog - Ghostpocalypse - Kevin MacLeod.mp3",
    "Measured Paces - Kevin MacLeod.mp3",
]
MUSIC_FINALE_TRACK = "March of the Mind - Kevin MacLeod.mp3"


def _other_tracks():
    """Every track in music/ that is not one of the fixed chapter tracks —
    the pool the middle chapters draw from."""
    reserved = set(MUSIC_OPENING_TRACKS) | {MUSIC_FINALE_TRACK}
    return [t for t in _music_tracks() if os.path.basename(t) not in reserved]


def plan_chapter_score(chapter_marks, total: float):
    """One track per chapter, as [(track_path_or_None, start, end), ...].

    MUSIC_OPENING_TRACKS in order for chapters 1-4, MUSIC_FINALE_TRACK for
    the LAST chapter — which wins over the "first four" rule on a video
    short enough that the two would otherwise collide — and a random pick
    from _other_tracks() for anything in between. A named track that is
    not actually sitting in music/ yet, or an empty pool for a middle
    chapter, resolves to None for that chapter rather than raising; see
    build_score for what None renders as.

    Consecutive chapters that land on the identical track are merged into
    one act — crossfading a track into itself is wasted work and an
    audible restart for no reason.
    """
    if not chapter_marks or total <= 0:
        return []

    other = _other_tracks()
    last_idx = len(chapter_marks) - 1

    def resolve(i):
        if i == last_idx:
            name = MUSIC_FINALE_TRACK
        elif i < len(MUSIC_OPENING_TRACKS):
            name = MUSIC_OPENING_TRACKS[i]
        else:
            return random.choice(other) if other else None
        path = os.path.join(MUSIC_DIR, name)
        return path if os.path.isfile(path) else None

    raw = []
    for i, (_, start) in enumerate(chapter_marks):
        end = chapter_marks[i + 1][1] if i < last_idx else total
        raw.append((resolve(i), start, end))

    acts = [raw[0]]
    for track, start, end in raw[1:]:
        if track == acts[-1][0]:
            acts[-1] = (acts[-1][0], acts[-1][1], end)
        else:
            acts.append((track, start, end))
    return acts


def build_score(acts, total: float, out: str):
    """Renders the acts into one continuous bed, crossfading at each track
    change. acts is [(track_path_or_None, start, end), ...] — see
    plan_chapter_score. A None track (a named chapter track that turned out
    not to be in music/, or an empty middle pool) renders as silence for
    that stretch rather than failing the whole bed.

    Returns the path, or None — a failure here costs the video its music, not
    the run, and a silent bed beats no video.
    """
    if not acts:
        return None
    try:
        n = len(acts)

        def _input_args(track, length):
            if track:
                return ["-stream_loop", "-1", "-t", f"{length:.3f}", "-i", track]
            # No track for this stretch (see plan_chapter_score) — silence,
            # not a missing input, so the act count and crossfade math stay
            # identical whether every named track was present or not.
            return ["-f", "lavfi", "-t", f"{length:.3f}",
                    "-i", "anullsrc=r=44100:cl=stereo"]

        if n == 1:
            track, start, end = acts[0]
            _run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                  *_input_args(track, total),
                  "-c:a", "pcm_s16le", "-ar", "44100", "-ac", "2", out])
            return out

        # acrossfade eats SCORE_CROSSFADE seconds per join, so each segment is
        # rendered that much longer and the sum still lands on `total`.
        inputs, names = [], []
        for i, (track, start, end) in enumerate(acts):
            length = (end - start) + (SCORE_CROSSFADE if i < n - 1 else 0.0)
            inputs += _input_args(track, length)
            names.append(f"[{i}:a]")

        fc, prev = [], names[0]
        for i in range(1, n):
            label = f"[x{i}]" if i < n - 1 else "[out]"
            fc.append(f"{prev}{names[i]}acrossfade=d={SCORE_CROSSFADE}"
                      f":c1=tri:c2=tri{label}")
            prev = label
        _run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *inputs,
              "-filter_complex", ";".join(fc), "-map", "[out]",
              "-t", f"{total:.3f}", "-c:a", "pcm_s16le",
              "-ar", "44100", "-ac", "2", out])
        listing = ", ".join(
            os.path.basename(t).rsplit(" - ", 1)[0] if t else "(silence)"
            for t, _, _ in acts)
        print(f"🎼 Score in {n} act(s): {listing}")
        return out
    except Exception as e:
        print(f"⚠️ Score assembly failed ({type(e).__name__}: {e}) — "
              f"falling back to a single track.")
        return None


# ---------------------------------------------------------------------------
# Sound design
#
# Everything above this line is picture. The audio track, for seven minutes,
# was narration plus one music file at a fixed 10% — the same bed, at the same
# level, under the hook, under the midpoint and under the closing line. Not one
# auditory event in the whole video. Every pattern interrupt the channel had
# was something you could only see, which means it had none at all for anyone
# half-watching in another tab, and half-watching is most of the audience.
#
# Two things are added here, both synthesised by ffmpeg at render time. No
# files to license, no downloads to fail in CI, no repository bloat:
#
#   an impact   a short low hit under each number callout, so the figure lands
#
# A riser under each chapter card was tried and cut: on the first published
# video it read as a swoosh under sliding text, which is the one thing this
# channel's voice cannot carry. The generator still knows how to make one —
# _sfx_sources takes "riser" — but nothing asks for it any more.
#
# And the score itself now moves. Not just in level: the TRACK changes at the
# midpoint and again at the cost, so the reset a viewer hears is a different
# piece of music rather than the same loop at a different volume.
# ---------------------------------------------------------------------------

# Held low on purpose. These are punctuation, not effects — the moment a viewer
# notices the sound design as sound design, it reads as a template channel.
IMPACT_GAIN = 0.28
RISER_GAIN = 0.16

# The bed, and how far it moves. All five lowered ~30% (same ratio as the
# Shorts music cut) after the owner tested a real render and called it too
# loud — proportions between the terms are unchanged, only the baseline.
MUSIC_BASE = 0.06
MUSIC_CARD_LIFT = 0.025        # under a chapter card
MUSIC_PIVOT_DUCK = 0.038       # into the midpoint — nearly out
MUSIC_PIVOT_SWELL = 0.038      # and back up, over the top, as it opens
MUSIC_OUTRO_LIFT = 0.032       # under the closing lines and the end card

# Seconds either side of a move, so levels ramp instead of stepping. A hard
# jump in a music bed is audible as a click, which is worse than no move.
MUSIC_RAMP = 0.8


def _bump(a: float, b: float, fade: float = MUSIC_RAMP) -> str:
    """An ffmpeg volume-expression term: 0 outside [a,b], 1 inside, ramped."""
    a, b = max(0.0, a), max(0.0, b)
    fade = max(0.05, min(fade, (b - a) / 2 if b > a else 0.05))
    return (f"min(min(1,max(0,(t-{a:.3f})/{fade:.3f})),"
            f"min(1,max(0,({b:.3f}-t)/{fade:.3f})))")


def music_envelope(card_starts, pivot_start, total: float) -> str:
    """The music bed's level as a function of time, as an ffmpeg expression.

    Everything is additive against MUSIC_BASE, and every term is a ramped
    window, so the result is continuous and never goes negative.
    """
    terms = [f"{MUSIC_BASE:.4f}"]

    for start in card_starts or []:
        if pivot_start is not None and abs(start - pivot_start) < 4.0:
            continue                      # the pivot has its own, bigger move
        terms.append(f"{MUSIC_CARD_LIFT:.4f}*{_bump(start - 0.6, start + 3.4)}")

    if pivot_start is not None:
        # Out, then over the top. The duck starts before the chapter does,
        # under the beat of silence the audio engine left there.
        terms.append(f"-{MUSIC_PIVOT_DUCK:.4f}*"
                     f"{_bump(pivot_start - 2.2, pivot_start + 1.2)}")
        terms.append(f"{MUSIC_PIVOT_SWELL:.4f}*"
                     f"{_bump(pivot_start + 1.4, pivot_start + 6.5)}")

    if total > 20:
        terms.append(f"{MUSIC_OUTRO_LIFT:.4f}*{_bump(total - 13.0, total)}")

    return "+".join(terms).replace("+-", "-")


def _sfx_sources(cues):
    """(filter_complex fragments, output labels) for every synthesised hit.

    impact: two low sines an octave apart with a fast decay — felt more than
            heard, which is what a number callout wants.
    riser:  a tone sweeping upward over a second, ending where the card lands.
    """
    frags, labels = [], []
    for i, (kind, at) in enumerate(cues):
        label = f"sfx{i}"
        delay = max(0, int(at * 1000))
        if kind == "impact":
            # exp decay over 1.1s, fundamental at 58Hz with an octave above.
            expr = ("(sin(2*PI*58*t)*0.7+sin(2*PI*116*t)*0.3)"
                    "*exp(-3.4*t)")
            dur, gain = 1.2, IMPACT_GAIN
        else:
            # 1.0s sweep, 180Hz -> 780Hz, fading in so it does not click.
            expr = "sin(2*PI*t*(180+600*t))*min(1,t*3)*exp(-1.1*t)"
            dur, gain = 1.0, RISER_GAIN
        frags.append(
            f"aevalsrc='{expr}':s=44100:d={dur}[{label}r];"
            f"[{label}r]volume={gain},aformat=channel_layouts=stereo,"
            f"adelay={delay}|{delay}[{label}]")
        labels.append(label)
    return frags, labels


def build_sfx_bed(cues, total: float, out: str):
    """Renders every hit into one silent-background track the length of the
    video, so the final mix takes one extra input instead of twenty.

    Returns the path, or None — sound design is the first thing that should be
    dropped if ffmpeg is unhappy, because the video is fine without it.
    """
    cues = [(k, t) for k, t in (cues or []) if 0 <= t < total]
    if not cues:
        return None
    try:
        frags, labels = _sfx_sources(cues)
        # A silent track the full length of the video is the first input, so
        # the mix is exactly as long as the video without padding after the
        # fact — apad after an amix does not reliably reach the target.
        frags.insert(0, f"anullsrc=r=44100:cl=stereo:d={total:.3f}[base]")
        labels.insert(0, "base")
        mix = "".join(f"[{l}]" for l in labels)
        chain = (";".join(frags) + ";" +
                 f"{mix}amix=inputs={len(labels)}:duration=first:normalize=0"
                 f"[out]")
        _run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
              "-filter_complex", chain, "-map", "[out]",
              "-c:a", "pcm_s16le", "-ar", "44100", "-ac", "2", out])
        print(f"🔔 Sound design: {len(cues)} cues "
              f"({sum(1 for k, _ in cues if k == 'impact')} impacts, "
              f"{sum(1 for k, _ in cues if k == 'riser')} risers).")
        return out
    except Exception as e:
        print(f"⚠️ Sound design skipped ({type(e).__name__}: {e}).")
        return None


def generate_longform_video(scenes, build_dir="build"):
    """Renders the finished video. Returns (path, total_seconds, chapter_marks,
    music_attribution) where chapter_marks is [(heading, start_seconds), ...]
    for the description, and music_attribution is the required credit text
    for any Creative Commons track used in the score, or "" if none."""
    print("🎬 Long-form video engine starting...")
    usable = [s for s in scenes if s.get("image_path") and s.get("audio_path")]

    # ---- Phase 1: scene clips ----
    for idx, s in enumerate(usable):
        s["_clip"] = os.path.join(build_dir, f"lf_clip_{idx:03d}.mp4")
        shots = len(s.get("image_paths") or [1])
        kind = "b-roll" if s.get("video_path") else f"{shots} shot(s)"
        print(f"🎞️  Clip {idx + 1}/{len(usable)} ({s['duration']:.1f}s, {kind})...")
        _scene_clip(s, s["_clip"], idx % 8)

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
    stat_cues, chapter_titles = [], []
    # Where the music ducks and the sound design fires.
    card_starts, pivot_start = [], None
    for c_idx, group in chapters:
        reel = os.path.join(build_dir, f"lf_reel_{c_idx:02d}.mp4")
        print(f"🔗 Chapter {c_idx + 1}: crossfading {len(group)} scenes...")
        offs, total = _xfade_chapter([g["_clip"] for g in group],
                                     [g["duration"] + XFADE for g in group],
                                     reel, cut_index=cuts)
        cuts += max(0, len(group) - 1)
        heading = group[0].get("chapter_heading", f"Part {c_idx + 1}")
        chapter_marks.append((heading, cursor))
        # Every chapter is announced on screen except the first. The opening
        # card used to slide in at 0.3s and sit over the hook for four seconds,
        # so the first thing a viewer read was a filing label rather than the
        # story — and 69% of them left inside the first 23 seconds. A chapter
        # title is orientation, and nobody needs orienting before they have
        # been given a reason to stay.
        is_pivot = bool(group[0].get("is_pivot"))
        if c_idx > 0:
            chapter_titles.append((cursor + 0.3, group[0]["duration"],
                                   c_idx + 1, heading, is_pivot))
            card_starts.append(cursor + 0.3)
        if is_pivot:
            pivot_start = cursor
        for g, o in zip(group, offs):
            global_offsets.append(cursor + o)

        # A chapter's headline number, held over whatever frame is on screen.
        # Synced to the moment it is actually spoken when the narration
        # contains it in numeral form; otherwise falls back to the old
        # position guess (after the first scene, so it never lands on top of
        # the chapter title that opens the chapter) rather than showing
        # nothing.
        stat = group[0].get("key_stat") or {}
        spoken_at = (_find_stat_value_time(group, offs, stat["value"])
                    if stat.get("value") else None)
        if spoken_at is not None:
            stat_start = cursor + spoken_at
        else:
            stat_start = cursor + max(group[0]["duration"] + 1.0, total * 0.35)
        if (stat.get("value") and total > STAT_HOLD + 4
                and stat_start + STAT_HOLD < cursor + total - 1.5):
            stat_cues.append((stat_start, str(stat["value"]),
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
    generate_subtitles(usable, ass_path, global_offsets, stat_cues=stat_cues,
                       chapter_titles=chapter_titles,
                       total_duration=total_duration)

    # The uploadable copy of the same words. Seven minutes of narration is the
    # most indexable thing this channel produces and none of it has ever been
    # readable by anything but a viewer's eyes.
    write_srt(usable, global_offsets, build_dir)

    print("🔥 Final render: grade, grain, captions, music...")
    final_output = os.path.join(build_dir, "final_longform.mp4")
    safe_ass = os.path.abspath(ass_path).replace("\\", "/").replace(":", "\\:")
    safe_fonts = os.path.abspath(FONTS_DIR).replace("\\", "/").replace(":", "\\:")

    # Animated film grain sits under the captions: it makes stills and archival
    # photos read as footage, and it costs seconds, not minutes, in the encode.
    vf = ("vignette=PI/5,"
          "eq=contrast=1.04:saturation=0.94,"
          "noise=alls=6:allf=t+u,"
          f"ass='{safe_ass}':fontsdir='{safe_fonts}',"
          f"fade=t=in:st=0:d=0.8,"
          f"fade=t=out:st={max(total_duration - 1.0, 0):.2f}:d=1.0")

    # Rendered to one track so the mix below stays a three-input graph however
    # many cues there are.
    # Number callouts only. There was a riser under every chapter card too; the
    # channel owner's verdict on the first published cut was that a swoosh under
    # sliding text reads as silly, and he is right — it is the one piece of
    # sound design that announces itself, on a channel whose whole voice is
    # that it does not. The impacts stay: they sit under a figure, they are
    # felt rather than heard, and nothing was wrong with them.
    sfx_cues = [("impact", start) for start, _, _ in stat_cues]
    sfx = build_sfx_bed(sfx_cues, total_duration,
                        os.path.join(build_dir, "lf_sfx.wav"))

    inputs, mix_parts, mix_labels = ["-i", merged], [], ["[0:a]"]

    # The score: one fixed track per chapter — see plan_chapter_score.
    acts = plan_chapter_score(chapter_marks, total_duration)
    score = build_score(acts, total_duration,
                        os.path.join(build_dir, "lf_score.wav")) if acts else None
    used_tracks = (sorted({t for t, _, _ in acts if t}) if score else [])
    if score is None:
        score = _pick_music()
        if score:
            used_tracks = [score]
            print(f"🎵 Bed: {os.path.basename(score)}")

    # Most tracks need nothing here (YouTube Audio Library, "Attribution not
    # required"), but a Creative Commons track only stays license-compliant
    # if its exact credit text is in the description — see music_attribution.py.
    # An act's score can draw on more than one track, so this can be more
    # than one credit block.
    music_attribution = "\n\n".join(
        filter(None, (attribution_for(t) for t in used_tracks)))

    if score:
        idx = len(inputs) // 2
        inputs += ["-i", score]
        # Level still moves inside an act: it ducks into the midpoint and
        # swells for the closing lines — see music_envelope.
        envelope = music_envelope(card_starts, pivot_start, total_duration)
        mix_parts.append(
            f"[{idx}:a]aloop=loop=-1:size=2e9,atrim=0:{total_duration:.3f},"
            f"volume=volume='{envelope}':eval=frame,"
            f"afade=t=out:st={max(total_duration - 2.5, 0):.2f}:d=2.5[bg]")
        mix_labels.append("[bg]")

    if sfx:
        idx = len(inputs) // 2
        inputs += ["-i", sfx]
        mix_labels.append(f"[{idx}:a]")

    if len(mix_labels) > 1:
        mix_parts.append(
            f"{''.join(mix_labels)}amix=inputs={len(mix_labels)}:"
            f"duration=first:normalize=0,"
            f"afade=t=out:st={max(total_duration - 1.2, 0):.2f}:d=1.2[aout]")
        cmd = ["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(mix_parts),
               "-map", "0:v", "-map", "[aout]", "-vf", vf,
               "-c:v", "libx264", "-preset", "medium", "-crf", "21",
               "-c:a", "aac", "-b:a", "192k", final_output]
    else:
        cmd = ["ffmpeg", "-y", "-i", merged, "-vf", vf,
               "-c:v", "libx264", "-preset", "medium", "-crf", "21",
               "-c:a", "copy", final_output]
    _run(cmd)

    print(f"✅ Long-form render complete: {final_output} "
          f"({total_duration / 60:.1f} min, {len(chapter_marks)} chapters)")
    return final_output, total_duration, chapter_marks, music_attribution
