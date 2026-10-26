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

from PIL import ImageFont

from caption_track import write_srt
from music_attribution import attribution_for

W, H = 1920, 1080
FPS = 30
XFADE = 0.5              # crossfade within a chapter
# the THEN / NOW opening (native_short.WIDE_SECONDS, ~8.5 s), laid over the
# first chapter's reel, then that chapter's own pictures
from native_short import WIDE_SECONDS as OPENING_SECONDS
PUNCH_HOLD = 3.0         # seconds a punch-phrase callout stays on screen

# The Far Cry 6 style cold-open card: black screen, an original line (or a
# short quote of a few sentences) fades in red then settles white, holds,
# then everything but its marked keyword fades away while the keyword itself
# turns red and lingers alone. No narration plays under it — nothing is
# actually being narrated at this point in the video anyway — but "The
# Claim"'s own score starts right here rather than waiting for the card to
# end: the card is scored as the opening of "The Claim", not the tail of
# "The Cost" (see the chapter-marks override in generate_longform_video).
#
# Each sentence fades in as ONE simultaneous unit (all its lines together,
# whether it wrapped to one physical line or, rarely, two — never staggered
# line by line); the next sentence does not begin until INTER_SENTENCE_WAIT
# after the previous one has fully settled to white. So the card's total
# length is not fixed — a longer quote takes longer to read in — but
# everything after the last sentence settles follows the same fixed shape
# every time:
#   settle -> INTRO_HOLD (hold) -> BODY_FADE (body fades, keyword -> red)
#   -> KEYWORD_HOLD (keyword alone) -> KEYWORD_FADE (keyword fades)
#   -> BLACK_BUFFER (black)
# A single short sentence with no attribution reduces to exactly the
# fixed 10.0s card (1.5 + 4.0 + 1.0 + 2.0 + 1.0 + 0.5).
# Shortened 2026-10-09 (owner): long-form viewers left at ~2:20 on average,
# and the cold-open card was 10-19 silent seconds at ~0:45. The quote now
# stays up as long as it takes to read (READ_SECONDS_PER_WORD), not a fixed
# four seconds after a slow cascade.
SENTENCE_FADE = 0.6
INTER_SENTENCE_WAIT = 0.4
INTRO_HOLD = 1.5              # the least time the full quote stays up
READ_SECONDS_PER_WORD = 0.25
BODY_FADE = 0.5
KEYWORD_HOLD = 1.2
KEYWORD_FADE = 0.5
BLACK_BUFFER = 0.3
ATTRIBUTION_FONT_SIZE = 40  # smaller than the quote's own 66pt Epigraph style
RED_ASS = r"&H0000FF&"
WHITE_ASS = r"&HFFFFFF&"

# Must match the Epigraph style's own Fontsize/MarginL/MarginR below — the
# width real sentences are wrapped against is only correct if it does. Wide
# enough that a normal sentence (a dozen-odd words) reads as one line, the
# way an actual Far Cry 6 card runs text nearly edge to edge — only a
# genuinely long sentence should ever spill to a second line.
EPIGRAPH_FONT_SIZE = 66
EPIGRAPH_MARGIN = 100
EPIGRAPH_MAX_WIDTH = W - 2 * EPIGRAPH_MARGIN

FONT_NAME = "Anton"
STAMP_FONT = "FreeMono"   # fonts/FreeMonoBold.ttf — the typewriter stamps

# ---------------------------------------------------------------------------
# Rhythm: each act looks, cuts and sounds different
#
# Approved from tools/render_rhythm_preview.py (v2). Seven minutes of one
# grade, one voice and one slow zoom is what made the videos feel like one
# long beat. Three changes break that up:
#
# 1. A colour grade per chapter role. B&W archive photos take a tint well, so
#    even an all-monochrome video reads as different places: the plan warm
#    and faded, the decision a dark red tint (deliberately subtle — the
#    owner found a stronger wash too much), the cost and the chain cold.
# 2. Typewriter date-and-place stamps where the story moves (see
#    longform_llm._chapter_stamps — only places and dates the chapter's own
#    narration states, checked in code).
# 3. Before "The Decision": an echoing clock rises from -30 dB while the
#    music slides down, one second of black and silence, then a low hit as
#    the chapter opens.
# ---------------------------------------------------------------------------
GRADES = {
    "warm": ("colorchannelmixer=.393:.769:.189:0:.349:.686:.168:0:.272:.534:.131,"
             "eq=saturation=0.85:contrast=0.9:brightness=0.02,"
             "curves=all='0/0.08 1/0.93'"),
    "red": ("eq=saturation=0.35:contrast=1.18:brightness=-0.02,"
            "colorbalance=rs=.23:gs=-.06:bs=-.08:rm=.16:gm=-.04:bm=-.06,"
            "vignette=PI/3.6"),
    "cold": ("colorbalance=rs=-.10:gs=-.02:bs=.14:rm=-.06:bm=.10,"
             "eq=saturation=0.4:contrast=1.15:brightness=-0.04,"
             "vignette=PI/4"),
}
# By pacing role (longform_llm.PACING). The claim and the verdict keep the
# neutral look: they are the narrator speaking now, not the past.
GRADE_BY_ROLE = {"open": "cold", "build": "warm", "pivot": "red",
                 "drive": "cold"}

SILENCE_BEFORE_DECISION = 1.0
CLOCK_SPAN = 6.0            # the clock only plays under the build-up's end
CLOCK_START_DB = -30        # enters barely audible...
CLOCK_PEAK_DB = -6          # ...and is clearly there by the silence
MUSIC_UNDER_CLOCK = 0.3     # music slides down to 30% as the clock rises
HIT_GAIN = 0.45             # peaks level with the narration, not above it
KEYCLICK_GAIN = 0.08        # present, never in the way of the voice
TYPE_RATE = 0.055           # seconds per typed stamp character
STAMP_HOLD = 2.6
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
Style: Punch,{FONT_NAME},84,&H0000E8FF,&H00FFFFFF,&H00000000,&HB0000000,0,0,0,0,100,100,1,0,1,3,2,8,130,130,130,1
Style: Chapter,{FONT_NAME},110,&H00FFFFFF,&H0000E8FF,&H00000000,&HB0000000,0,0,0,0,100,100,1,0,1,4,3,4,150,150,60,1
Style: Cta,{FONT_NAME},64,&H0000E8FF,&H00FFFFFF,&H00000000,&HB0000000,0,0,0,0,100,100,2,0,1,4,3,2,140,140,200,1
Style: Pivot,{FONT_NAME},128,&H00FFFFFF,&H0000E8FF,&H00000000,&HB0000000,0,0,0,0,100,100,2,0,1,5,4,5,120,120,60,1
Style: Scrim,{FONT_NAME},10,&H00000000,&H00000000,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
Style: Stamp,{STAMP_FONT},39,&H00E8F0F0,&H00FFFFFF,&H00000000,&H64000000,1,0,0,0,100,100,3,0,1,2,0,7,78,78,66,1
Style: Epigraph,{FONT_NAME},{EPIGRAPH_FONT_SIZE},&H00FFFFFF,&H0000E8FF,&H00000000,&H00000000,1,0,0,0,100,100,1,0,1,0,0,5,{EPIGRAPH_MARGIN},{EPIGRAPH_MARGIN},0,1

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


LIFTED_MARGIN_V = 250       # captions sit above the date ticker


def _overlaps(start, end, periods):
    return any(start < b and end > a for a, b in (periods or ()))


def _credit_events(scene: dict, offset: float, lift_periods=None):
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
        cy = H - CREDIT_MARGIN_Y - (210 if _overlaps(start, end, lift_periods) else 0)
        fx = (f"{{\\an1\\pos({CREDIT_MARGIN_X},{cy})\\fad(200,200)"
              f"\\fs{CREDIT_FONT_SIZE}\\c&HD8D8D8&\\bord2\\shad1}}")
        events.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Body,,0,0,0,,{fx}{text}")
    return events


def _source_events(scene: dict, offset: float, lift_periods=None):
    """The claim's source, bottom right (docs/VIDEO_SPEC.md): "SOURCE" in the
    accent colour, then the work in the image credit's grey, for the whole
    scene. Not over a map or a cast card, whose looks were approved as they
    are."""
    src = scene.get("claim_source") or {}
    label = (src.get("label") or "").replace("{", "").replace("}", "")
    duration = float(scene.get("duration") or 0.0)
    if (not label or duration < 1.5 or scene.get("_is_map") or scene.get("map")
            or scene.get("dossier")):
        return []
    start, end = offset, offset + duration
    cy = H - CREDIT_MARGIN_Y - (210 if _overlaps(start, end, lift_periods) else 0)
    fx = (f"{{\\an3\\pos({W - CREDIT_MARGIN_X},{cy})\\fad(250,250)"
          f"\\fs{CREDIT_FONT_SIZE}\\bord2\\shad1}}")
    return [f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Body,,0,0,0,,"
            f"{fx}{{\\c{ACCENT_ASS}}}SOURCE {{\\c&HD8D8D8&}}{label}"]


_PART_WORDS = ["ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT"]


# Where the accent rule sits beneath the phrase, and how wide it draws in.
# Hand-placed against the Punch style's own metrics (84pt, up to two lines,
# starting at MarginV=130) and confirmed against a real rendered frame —
# see the comment on _punch_phrase_event.
PUNCH_RULE_Y = 340
PUNCH_RULE_WIDTH = 460

# A phrase still being spoken when PUNCH_HOLD would end it holds this much
# longer past the last word instead — never disappears mid-word, never
# lingers long after the words that earned it are gone.
PUNCH_TAIL = 1.2


def _punch_phrase_event(start: float, end_spoken: float, phrase: str):
    """A short (2-6 word) phrase lifted verbatim from the narration (see
    longform_llm._extract_punch_phrase), flashed on screen for the moment
    it is actually being said. Returns [rule, text] — two Dialogue lines,
    since ASS has no way to layer two effects in one event.

    Replaces the old "value \\N label" number callout: a real render
    showed the model can correctly pick a true, striking figure that it
    never actually states in the narration, so nothing could ever
    reliably sync a NUMBER to speech. A verbatim phrase can't have that
    problem — it IS the words being spoken, so the callout's timing comes
    directly from where those words fall in the scene's own word timings,
    not from a separate value hunted for afterward.

    Same "detail snapping into focus" reveal as the callout this replaces
    (small, blurred and transparent, punches past full size, settles back
    — under 300ms, reads as impact not wobble) and the same drawn accent
    rule underneath, reusing _pivot_events' \\p1 vector-rectangle
    technique — this and the pivot card are the video's two "big graphic
    moment" beats, and share one visual language on purpose.
    """
    hold = max(PUNCH_HOLD, (end_spoken - start) + PUNCH_TAIL)
    end = start + hold
    # Two non-overlapping \t() stages per tag — libass's handling of two
    # \t() blocks racing the SAME tag over overlapping windows is undefined,
    # which an earlier draft of this did with \be (blur cleared by both a
    # 0-90ms and a 0-180ms block at once). Stage 1 (0-180ms) resolves alpha,
    # blur and the overshoot scale/rotation together; stage 2 (180-260ms)
    # settles scale and rotation back to rest.
    punch = (
        r"{\an8\alpha&HFF&\be8\fscx55\fscy55\frz-3"
        r"\t(0,180,\alpha&H00&\fscx116\fscy116\frz2\be0)"
        r"\t(180,260,\fscx100\fscy100\frz0)}"
    )
    body = phrase.replace("{", "").replace("}", "").upper()
    text_line = f"Dialogue: 1,{_ass_time(start)},{_ass_time(end)},Punch,,0,0,0,,{punch}{body}"

    rule_x = W // 2 - PUNCH_RULE_WIDTH // 2
    rule = (
        f"{{\\an4\\pos({rule_x},{PUNCH_RULE_Y})\\1c{ACCENT_ASS}\\1a&H30&"
        f"\\bord0\\shad0\\fscx0\\fad(0,300)\\t(60,260,\\fscx100)\\p1}}"
        f"m 0 0 l {PUNCH_RULE_WIDTH} 0 l {PUNCH_RULE_WIDTH} 5 l 0 5{{\\p0}}"
    )
    rule_line = f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Scrim,,0,0,0,,{rule}"
    return [rule_line, text_line]


def _find_phrase_time(scene, phrase: str):
    """(start, end) local to this ONE scene for the exact run of words
    matching phrase, found in the scene's own word timings (real or
    estimated — see audio_engine.py's _estimate_word_timings).

    Replaces the old key_stat design, which asked the model for a separate
    number+label and then had to search the whole chapter afterward hoping
    it was actually said — including handling spelled-out vs numeral forms,
    since it often was not said as written. That entire search is gone: the
    phrase is now a verbatim quote extracted directly from this scene's own
    narration (see longform_llm._extract_punch_phrase), so it is guaranteed
    to appear in this scene's words in exactly this form — this only needs
    to find WHERE, not guess whether.

    Returns None only if word timings are missing entirely for this scene
    (should not happen post the Kokoro/gTTS estimated-timing fix, but a
    caller must never crash for want of a callout).
    """
    words = scene.get("words") or []
    if not words:
        return None
    targets = [re.sub(r"[^a-z0-9]", "", w.lower()) for w in phrase.split()]
    tokens = [re.sub(r"[^a-z0-9]", "", (w.get("word") or "").lower())
             for w in words]
    n = len(targets)
    for i in range(len(tokens) - n + 1):
        if tokens[i:i + n] == targets:
            return float(words[i]["start"]), float(words[i + n - 1]["end"])
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


def _stamp_events(start: float, text: str):
    """A date-and-place stamp typed in one character at a time behind a block
    cursor, held, then faded. Top-left, clear of the captions (bottom), the
    chapter title (middle-left) and the punch phrase (top-centre)."""
    events = []
    for i in range(1, len(text) + 1):
        a, b = start + (i - 1) * TYPE_RATE, start + i * TYPE_RATE
        events.append(f"Dialogue: 2,{_ass_time(a)},{_ass_time(b)},Stamp,,0,0,0,,"
                      f"{text[:i]}█")
    typed = start + len(text) * TYPE_RATE
    events.append(f"Dialogue: 2,{_ass_time(typed)},{_ass_time(typed + STAMP_HOLD)},"
                  f"Stamp,,0,0,0,,{{\\fad(0,500)}}{text}")
    return events


def generate_subtitles(scenes, output_ass, offsets, punch_cues=None,
                       chapter_titles=None, total_duration=0.0,
                       stamp_cues=None, lift_periods=None):
    """offsets[i] is the global start time of scene i on the final timeline.
    punch_cues is [(start_seconds, end_spoken_seconds, phrase), ...] for the
    punch-phrase callouts.
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
                # above the date ticker while it is showing
                mv = LIFTED_MARGIN_V if _overlaps(start, end, lift_periods) else 0
                events.append(
                    f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Body,,0,0,{mv},,"
                    f"{_style_chunk(text)}")
        else:
            dur, sentence = scene["duration"], scene.get("narration", "")
            step = dur / max(1, (len(sentence) // 70) + 1)
            parts = [sentence[i:i + 70] for i in range(0, len(sentence), 70)] or [sentence]
            for p, part in enumerate(parts):
                events.append(
                    f"Dialogue: 0,{_ass_time(offset + p * step)},"
                    f"{_ass_time(offset + (p + 1) * step)},Body,,0,0,0,,{part.strip()}")

        events.extend(_credit_events(scene, offset, lift_periods))
        events.extend(_source_events(scene, offset, lift_periods))

    # Punch-phrase callouts, top of frame. _punch_phrase_event returns
    # [rule, text] — two events, since the accent rule and the phrase are
    # separate layers.
    for start, end_spoken, phrase in (punch_cues or []):
        events.extend(_punch_phrase_event(start, end_spoken, phrase))

    for cue in (chapter_titles or []):
        # (start, duration, number, heading[, is_pivot])
        start, duration, number, heading = cue[:4]
        is_pivot = bool(cue[4]) if len(cue) > 4 else False
        if is_pivot:
            events.extend(_pivot_events(start, duration, number, heading))
        else:
            events.append(_chapter_title_event(start, duration, number, heading))

    for start, text in (stamp_cues or []):
        events.extend(_stamp_events(start, text))

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
          f"({len(punch_cues or [])} punch-phrase callouts, "
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

def _anim_clip(scene, clip_len, style, graded, audio, tail):
    """An animated generated frame (animate_engine.py), made at its scene's
    length and fitted to it exactly: no frozen frame after the motion. A clip
    that would need more than a slight stretch is refused (Ken Burns then)."""
    from animate_engine import MAX_STRETCH
    anim = scene["anim_path"]
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                            "format=duration", "-of", "csv=p=0", anim],
                           capture_output=True, text=True)
    factor = clip_len / float(probe.stdout.strip())
    if factor > MAX_STRETCH:
        raise ValueError(f"the motion is {factor:.2f}x too short for its scene")
    vf = (f"setpts={factor:.4f}*PTS,scale={W}:{H}:force_original_aspect_ratio=increase"
          f":flags=lanczos,crop={W}:{H},setsar=1,fps={FPS}{graded}")
    _run(["ffmpeg", "-y", "-i", anim, "-i", audio, "-map", "0:v", "-map", "1:a",
          "-vf", vf, *tail])


def _scene_clip(scene, out, style):
    """One scene -> one clip, XFADE longer than its narration. Three shapes:
    - video_path: real muted b-roll, cover-cropped, looped to the narration.
    - one image: classic Ken Burns.
    - several images: Ken Burns per shot, crossfaded inside the scene, so a
      long narration segment no longer means a frozen frame."""
    audio, duration = scene["audio_path"], scene["duration"]
    clip_len = duration + XFADE
    # A map beat is a graphic drawn in its chapter's palette (map_engine);
    # a photo grade on top would turn its red action grey.
    grade = "" if scene.get("_is_map") else \
        GRADES.get(GRADE_BY_ROLE.get(scene.get("chapter_role"), ""), "")
    graded = f",{grade}" if grade else ""
    # The picture runs on past the last word; the audio is padded to match so
    # the muxed clip has one length and the dissolve has something to dissolve.
    tail = ["-af", f"apad=pad_dur={XFADE:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2",
            "-pix_fmt", "yuv420p", "-t", f"{clip_len:.3f}", out]

    if scene.get("anim_path"):
        try:
            _anim_clip(scene, clip_len, style, graded, audio, tail)
            return
        except Exception as e:
            print(f"   ↩️  Animated shot unusable ({type(e).__name__}: {e}) "
                  "— Ken Burns instead.")

    if scene.get("video_path"):
        vf = (f"scale={W}:{H}:force_original_aspect_ratio=increase,"
              f"crop={W}:{H},setsar=1,fps={FPS}{graded}")
        _run(["ffmpeg", "-y", "-stream_loop", "-1", "-i", scene["video_path"],
              "-i", audio, "-map", "0:v", "-map", "1:a", "-vf", vf, *tail])
        return

    images = scene.get("image_paths") or [scene["image_path"]]
    if len(images) == 1:
        _run(["ffmpeg", "-y", "-loop", "1", "-framerate", str(FPS),
              "-i", images[0], "-i", audio,
              "-vf", _ken_burns(clip_len, style) + graded, *tail])
        return

    durs = _shot_durations(clip_len, len(images))
    inputs = []
    for img, d in zip(images, durs):
        inputs += ["-loop", "1", "-framerate", str(FPS),
                   "-t", f"{d + 0.1:.3f}", "-i", img]
    inputs += ["-i", audio]

    fc = [f"[{i}:v]{_ken_burns(d, style + i * 3)}{graded}[z{i}]"
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
WHOOSH_GAIN = 0.3           # into a cast card's freeze (if its own sound fails)
CAST_SFX_GAIN = 0.8         # the cast card's own sound, native_cast.card_sfx
STAMPS_ENABLED = False
ROLL_GAIN = 0.05            # date ticker: a tick per day rolled past
DATEHIT_GAIN = 0.4          # date ticker: landing on the spoken date
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


def music_envelope(card_starts, pivot_start, total: float,
                   silence=None) -> str:
    """The music bed's level as a function of time, as an ffmpeg expression.

    Everything is additive against MUSIC_BASE, and every term is a ramped
    window, so the result is continuous and never goes negative.

    silence=(clock_start, silence_start, silence_end) multiplies the whole
    bed by a factor that slides from 1 to MUSIC_UNDER_CLOCK across the clock,
    is 0 through the black, and is 1 again from the hit.
    """
    expr = _music_additive(card_starts, pivot_start, total)
    if not silence:
        return expr
    c0, s0, s1 = silence
    span = max(0.1, s0 - c0)
    factor = (f"if(lt(t,{c0:.3f}),1,if(lt(t,{s0:.3f}),"
              f"1-{1 - MUSIC_UNDER_CLOCK:.3f}*(t-{c0:.3f})/{span:.3f},"
              f"if(lt(t,{s1:.3f}),0,1)))")
    return f"({expr})*({factor})"


def _music_additive(card_starts, pivot_start, total: float) -> str:
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
    for i, cue in enumerate(cues):
        kind, at = cue[0], cue[1]
        span = cue[2] if len(cue) > 2 else 0.0
        label = f"sfx{i}"
        delay = max(0, int(at * 1000))
        if kind == "clock":
            # One mechanical tick a second (a noise transient and two damped
            # metallic partials) with a long hall echo, rising from
            # CLOCK_START_DB to CLOCK_PEAK_DB across the span. The last echo
            # rings into the silence and is gone by its midpoint, so the hit
            # still lands on nothing.
            tail = SILENCE_BEFORE_DECISION / 2
            expr = ("((random(0)*2-1)*exp(-900*mod(t,1))*0.9"
                    "+sin(2*PI*2750*t)*exp(-160*mod(t,1))*0.55"
                    "+sin(2*PI*5600*t)*exp(-260*mod(t,1))*0.25)")
            ramp = (f"pow(10,({CLOCK_START_DB}+({CLOCK_PEAK_DB - CLOCK_START_DB})"
                    f"*t/{span:.3f})/20)")
            frags.append(
                f"aevalsrc='{expr}':s=44100:d={span + tail:.3f},"
                f"highpass=f=500,lowpass=f=8000,"
                f"aecho=0.8:0.88:170|340|560|820:0.5|0.34|0.22|0.12,"
                f"volume=eval=frame:volume='{ramp}',"
                f"afade=t=out:st={span:.3f}:d={tail:.3f}[{label}r];"
                f"[{label}r]aformat=channel_layouts=stereo,"
                f"adelay={delay}|{delay}[{label}]")
            labels.append(label)
            continue
        if kind == "keys":
            expr = (f"(random(0)*2-1)*exp(-140*mod(t,{TYPE_RATE}))"
                    f"*{KEYCLICK_GAIN}")
            frags.append(
                f"aevalsrc='{expr}':s=44100:d={span:.3f},highpass=f=1500[{label}r];"
                f"[{label}r]aformat=channel_layouts=stereo,"
                f"adelay={delay}|{delay}[{label}]")
            labels.append(label)
            continue
        if kind == "roll":
            # the ticker rolling day by day: a soft tick per day passed
            count = max(1, int(cue[3] if len(cue) > 3 else 10))
            period = max(0.035, span / count)
            frags.append(
                f"aevalsrc='(random(0)*2-1)*exp(-400*mod(t,{period:.4f}))*{ROLL_GAIN}'"
                f":s=44100:d={span:.3f},highpass=f=2500,"
                f"aformat=channel_layouts=stereo,adelay={delay}|{delay}[{label}]")
            labels.append(label)
            continue
        if kind == "datehit":
            frags.append(
                f"aevalsrc='(sin(2*PI*70*t)*0.6+(random(0)*2-1)*exp(-80*t)*0.3)*exp(-9*t)'"
                f":s=44100:d=0.6,volume={DATEHIT_GAIN},"
                f"aformat=channel_layouts=stereo,adelay={delay}|{delay}[{label}]")
            labels.append(label)
            continue
        if kind == "file":
            # a sound rendered ahead (a cast card's, native_cast.card_sfx)
            src = os.path.abspath(cue[2]).replace("\\", "/").replace(":", "\\:")
            frags.append(
                f"amovie='{src}',volume={CAST_SFX_GAIN},aformat=sample_rates=44100:"
                f"channel_layouts=stereo,adelay={delay}|{delay}[{label}]")
            labels.append(label)
            continue
        if kind == "whoosh":
            frags.append(
                f"anoisesrc=r=44100:d=0.4:c=pink:a=0.6,highpass=f=900,lowpass=f=6000,"
                f"afade=t=in:d=0.3,afade=t=out:st=0.3:d=0.1,volume={WHOOSH_GAIN},"
                f"aformat=channel_layouts=stereo,adelay={delay}|{delay}[{label}]")
            labels.append(label)
            continue
        if kind == "hit":
            expr = ("(sin(2*PI*48*t)*0.9+sin(2*PI*96*t)*0.35"
                    "+(random(0)*2-1)*exp(-22*t)*0.8)*exp(-2.2*t)")
            dur, gain = 2.2, HIT_GAIN
        elif kind == "impact":
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
    cues = [c for c in (cues or []) if 0 <= c[1] < total]
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
        kinds = [c[0] for c in cues]
        print(f"🔔 Sound design: {len(cues)} cues "
              + ", ".join(f"{kinds.count(k)} {k}" for k in dict.fromkeys(kinds))
              + ".")
        return out
    except Exception as e:
        print(f"⚠️ Sound design skipped ({type(e).__name__}: {e}).")
        return None


def _norm_word(w: str) -> str:
    return re.sub(r"[^a-z0-9]", "", w.lower())


_EPIGRAPH_FONT_CACHE = None


def _epigraph_font():
    """The real Anton font at the Epigraph style's own size, cached — used
    to measure actual pixel width for natural line-wrapping rather than
    guessing at a word count."""
    global _EPIGRAPH_FONT_CACHE
    if _EPIGRAPH_FONT_CACHE is None:
        path = os.path.join(FONTS_DIR, "Anton-Regular.ttf")
        _EPIGRAPH_FONT_CACHE = ImageFont.truetype(path, EPIGRAPH_FONT_SIZE)
    return _EPIGRAPH_FONT_CACHE


def _text_width(text: str) -> int:
    box = _epigraph_font().getbbox(text)
    return box[2] - box[0]


def _wrap_sentence(text: str, keyword: str):
    """Wraps one sentence only where a line would actually overflow the
    card, measured against the real font — a sentence stays on one line
    whenever it fits (which is most of the time; the margin is wide
    enough for a normal sentence), and only spills to a second line when
    it truly does not. The marked keyword is isolated inline within
    whichever natural line it falls on. Returns line_spans: one
    [(text, is_keyword)] list per physical line.
    """
    words = text.split()
    if not words:
        return [[(text, False)]]

    norm = [_norm_word(w) for w in words]
    kw_words = [_norm_word(w) for w in keyword.split()] if keyword else []
    kw_span = None
    if kw_words:
        n = len(kw_words)
        for i in range(len(norm) - n + 1):
            if norm[i:i + n] == kw_words:
                kw_span = (i, i + n)
                break

    # The keyword's own words are packed as one atomic token, never a
    # bare word — otherwise width-wrapping could split it across two
    # lines (as it silently did before this), losing the isolation
    # entirely without any error to say so.
    tokens, i = [], 0
    while i < len(words):
        if kw_span and i == kw_span[0]:
            tokens.append((" ".join(words[kw_span[0]:kw_span[1]]), True))
            i = kw_span[1]
        else:
            tokens.append((words[i], False))
            i += 1

    lines, current = [], []
    for tok, is_kw in tokens:
        trial = " ".join(t for t, _ in current + [(tok, is_kw)])
        if current and _text_width(trial) > EPIGRAPH_MAX_WIDTH:
            lines.append(current)
            current = [(tok, is_kw)]
        else:
            current.append((tok, is_kw))
    if current:
        lines.append(current)

    line_spans = []
    for line in lines:
        spans, buf = [], []
        for tok, is_kw in line:
            if is_kw:
                if buf:
                    spans.append((" ".join(buf), False))
                    buf = []
                spans.append((tok, True))
            else:
                buf.append(tok)
        if buf:
            spans.append((" ".join(buf), False))
        # Spans were joined by a single space in the original line; bake
        # that spacing back in now that they are separate override runs.
        spaced = [(t if idx == 0 else f" {t}", kw)
                  for idx, (t, kw) in enumerate(spans)]
        line_spans.append(spaced)
    return line_spans


def _wrap_quote_lines(sentences, keyword):
    """Wraps every sentence of a (possibly multi-sentence) quote, scanning
    them in order for the marked keyword and isolating only its first
    occurrence. Returns (groups, keyword_group) — groups is one
    line_spans list per sentence (see _wrap_sentence); keyword_group is
    (sentence_index, line_index) or None.
    """
    groups, keyword_group = [], None
    for s_idx, sentence in enumerate(sentences):
        kw_here = keyword if keyword_group is None else ""
        line_spans = _wrap_sentence(sentence, kw_here)
        for li, spans in enumerate(line_spans):
            if any(is_kw for _, is_kw in spans):
                keyword_group = (s_idx, li)
                break
        groups.append(line_spans)
    return groups, keyword_group


def _intro_card_schedule(epigraph: dict):
    """Works out the full timing for a cold-open card without writing
    anything: (groups, keyword_group, group_starts, attribution_start,
    total_duration). One source of truth shared by the ASS writer and the
    clip renderer, so the file on disk and the reported clip length can
    never drift apart.

    epigraph may carry either "sentences" (a list, cascading in one after
    another with a pause between each — see SENTENCE_FADE /
    INTER_SENTENCE_WAIT above) or the older single-line "text"; "keyword"
    marks the phrase that survives the exit, and an optional "attribution"
    (plus "attribution_title", e.g. a role — used when the name alone
    would mean nothing to most viewers) is shown as one more, smaller,
    unmarked beat after the quote.
    """
    sentences = epigraph.get("sentences") or [epigraph.get("text") or ""]
    sentences = [s.strip() for s in sentences if s and s.strip()]
    keyword = (epigraph.get("keyword") or "").strip()
    attribution = (epigraph.get("attribution") or "").strip()
    attribution_title = (epigraph.get("attribution_title") or "").strip()

    groups, keyword_group = _wrap_quote_lines(sentences, keyword)

    group_starts, t = [], 0.0
    for _ in groups:
        group_starts.append(t)
        t += SENTENCE_FADE + INTER_SENTENCE_WAIT
    # group_starts[-1] is when the last sentence itself begins fading in —
    # the trailing wait the loop just added is only there to seed a group
    # that may not exist, and is never folded into settle.
    settle = group_starts[-1] + SENTENCE_FADE if group_starts else 0.0

    attribution_start = None
    if attribution:
        attribution_start = settle + INTER_SENTENCE_WAIT
        settle = attribution_start + SENTENCE_FADE

    words = len(" ".join(sentences + [attribution]).split())
    hold_end = settle + max(INTRO_HOLD, READ_SECONDS_PER_WORD * words - settle)
    fade_to_kw_end = hold_end + BODY_FADE
    kw_hold_end = fade_to_kw_end + KEYWORD_HOLD
    kw_fade_end = kw_hold_end + KEYWORD_FADE
    total = kw_fade_end + BLACK_BUFFER

    return {
        "groups": groups, "keyword_group": keyword_group,
        "group_starts": group_starts, "attribution": attribution,
        "attribution_title": attribution_title,
        "attribution_start": attribution_start,
        "hold_end": hold_end, "fade_to_kw_end": fade_to_kw_end,
        "kw_hold_end": kw_hold_end, "kw_fade_end": kw_fade_end,
        "total": total,
    }


def _intro_card_ass(epigraph: dict, out_path: str) -> float:
    """Writes the standalone ASS script for the cold-open card and returns
    its total duration in seconds.

    One Dialogue event, not one per line: a line has to stay exactly where
    it sits once other lines around it fade, and separate events would mean
    re-positioning it — a visible jump instead of a clean disappearance.
    Every phrase line (and the attribution, if any) carries its own inline
    override block instead, so one event can give each sentence (and the
    keyword within it) its own timeline while still stacking as one
    paragraph (\\N is a hard break, not a wrap).
    """
    sched = _intro_card_schedule(epigraph)

    def ms(t):
        return int(round(t * 1000))

    def span_fx(start_ms, fade_ms, is_keyword, prefix=""):
        entry = (f"{prefix}\\c{RED_ASS}\\alpha&HFF&"
                 f"\\t({start_ms},{start_ms + fade_ms},\\alpha&H00&\\c{WHITE_ASS})")
        if is_keyword:
            return (f"{entry}"
                    f"\\t({ms(sched['hold_end'])},{ms(sched['fade_to_kw_end'])},\\c{RED_ASS})"
                    f"\\t({ms(sched['kw_hold_end'])},{ms(sched['kw_fade_end'])},\\alpha&HFF&)")
        return (f"{entry}"
                f"\\t({ms(sched['hold_end'])},{ms(sched['fade_to_kw_end'])},\\alpha&HFF&)")

    runs = []
    for g_idx, line_spans in enumerate(sched["groups"]):
        # Every line of this sentence shares the same entrance — a
        # sentence appears as one simultaneous unit, whether it wrapped to
        # one physical line or (rarely, for a long one) two; only the exit
        # (which span is the keyword) can differ within it.
        start = ms(sched["group_starts"][g_idx])
        for spans in line_spans:
            run_text = "".join(
                f"{{{span_fx(start, ms(SENTENCE_FADE), is_kw)}}}{span_text}"
                for span_text, is_kw in spans)
            runs.append(run_text)

    if sched["attribution"]:
        start = ms(sched["attribution_start"])
        fx = span_fx(start, ms(SENTENCE_FADE), False,
                     prefix=f"\\fs{ATTRIBUTION_FONT_SIZE}\\i1")
        label = f"— {sched['attribution']}"
        if sched["attribution_title"]:
            label += f", {sched['attribution_title']}"
        runs.append(f"{{{fx}}}{label}")

    text_block = r"\N".join(runs)
    event = (f"Dialogue: 0,{_ass_time(0)},{_ass_time(sched['kw_fade_end'])},"
             f"Epigraph,,0,0,0,,{text_block}")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(ASS_HEADER + event + "\n")
    return sched["total"]


def _build_intro_card(epigraph: dict, build_dir: str, name: str = "lf_intro"):
    """Renders the cold-open card as its own silent clip, encoded to match
    the chapter reels exactly so it concatenates into the same stream-copy
    join as everything else (see Phase 3 below). Returns (path,
    duration_seconds) — the card's length depends on how much it has to
    say, so the caller needs the real number, not an assumed constant.

    Each card needs its own file name: both cards used to render to
    lf_intro.mp4, so the pre-verdict card overwrote the opening card before
    the final join and the same quote played twice."""
    ass_path = os.path.join(build_dir, f"{name}.ass")
    duration = _intro_card_ass(epigraph, ass_path)
    out_path = os.path.join(build_dir, f"{name}.mp4")

    safe_ass = os.path.abspath(ass_path).replace("\\", "/").replace(":", "\\:")
    safe_fonts = os.path.abspath(FONTS_DIR).replace("\\", "/").replace(":", "\\:")
    vf = f"ass='{safe_ass}':fontsdir='{safe_fonts}'"

    _run(["ffmpeg", "-y",
          "-f", "lavfi", "-i", f"color=c=black:s={W}x{H}:r={FPS}",
          "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
          "-vf", vf,
          "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
          "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2",
          "-pix_fmt", "yuv420p", "-t", f"{duration:.3f}", out_path])
    return out_path, duration


def _build_black_clip(duration: float, build_dir: str) -> str:
    """A black, silent clip encoded like the chapter reels (see
    _build_intro_card), for the beat of silence before the decision."""
    out_path = os.path.join(build_dir, "lf_silence.mp4")
    _run(["ffmpeg", "-y",
          "-f", "lavfi", "-i", f"color=c=black:s={W}x{H}:r={FPS}",
          "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
          "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
          "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2",
          "-pix_fmt", "yuv420p", "-t", f"{duration:.3f}", out_path])
    return out_path


def generate_longform_video(scenes, build_dir="build", intro_epigraph=None,
                            verdict_epigraph=None, opening=None):
    """Renders the finished video. Returns (path, total_seconds, chapter_marks,
    music_attribution) where chapter_marks is [(heading, start_seconds), ...]
    for the description, and music_attribution is the required credit text
    for any Creative Commons track used in the score, or "" if none.

    intro_epigraph and verdict_epigraph, when given, are
    {"text"/"sentences", "keyword", ...} dicts from longform_llm's
    _source_intro_quote — narration-free title cards. intro_epigraph is
    inserted after the first chapter and before the second, scored as the
    second chapter's own opening rather than a silent gap; verdict_epigraph
    is inserted the same way immediately before the LAST chapter (a sad,
    weary pattern-interrupt ahead of the verdict), scored as that closing
    chapter's opening. Needs at least 3 chapters so the two insertion
    points cannot collide.

    opening, when given ({"now": (path, credit), "then": (path, credit) or
    None, "script": the script}), turns the first scene into the native
    Short's THEN / NOW opening: today's news, the tear, the year counter
    rolling back, the past sliding up (native_short.intro_clip_wide)."""
    print("🎬 Long-form video engine starting...")
    usable = [s for s in scenes if s.get("image_path") and s.get("audio_path")]

    # ---- Phase 1: scene clips ----
    map_targets = []        # where each rendered map landed, see map_engine
    for idx, s in enumerate(usable):
        s["_clip"] = os.path.join(build_dir, f"lf_clip_{idx:03d}.mp4")
        if idx == 0 and opening:
            # the first OPENING_SECONDS only, then the scene's own pictures
            # (the first render looped one newsreel street for 9 seconds)
            try:
                from native_short import intro_clip_wide
                then = opening.get("then")
                past = then[0] if then else (s.get("video_path") or s["image_path"])
                out = os.path.join(build_dir, "lf_opening.mp4")
                # its full length, over the first chapter rather than the
                # first line: a 4-second hook cut it before the years had
                # moved (owner, 26 Oct)
                import owner_media
                ow = owner_media.opening_settings()
                lines = tuple(ow.get("lines") or ()) or None
                s["_ticks"] = intro_clip_wide(opening["now"][0], past, OPENING_SECONDS,
                                              opening["script"], build_dir, out,
                                              then_year=ow.get("then_year"),
                                              then_date=ow.get("then_date"),
                                              captions=lines)
                s["_opening"] = out
                s["credit"] = [c for c in (opening["now"][1], then[1] if then else "") if c] \
                    + list(s.get("credit") or [])
                print(f"🕰️  THEN / NOW opening over the first {OPENING_SECONDS:.1f}s.")
            except Exception as e:
                print(f"⚠️ THEN / NOW opening failed ({type(e).__name__}: {e}) — plain opening.")
        if s.get("map"):
            # A map beat (up to two per video) replaces this scene's pictures
            # for its whole line; any failure leaves the photos as they were.
            from map_engine import render_map_scene
            mp = render_map_scene(s, os.path.join(build_dir, f"lf_map_{idx:03d}.mp4"),
                                  s["duration"] + XFADE, avoid=map_targets)
            if mp:
                s["video_path"], s["_is_map"], s["credit"] = mp, True, []
        elif s.get("chain"):
            from chain_engine import render_chain_scene
            cp = render_chain_scene(s, os.path.join(build_dir, f"lf_chain_{idx:03d}.mp4"),
                                    s["duration"] + XFADE)
            if cp:
                s["video_path"], s["_is_map"], s["credit"] = cp, True, []
        elif s.get("dossier"):
            from dossier_engine import render_dossier_scene
            got = render_dossier_scene(
                s, os.path.join(build_dir, f"lf_dossier_{idx:03d}.mp4"),
                s["duration"] + XFADE, build_dir, era=s.get("era_year"))
            if got:
                # the portrait is the one picture on screen: its Commons
                # credit spans the whole scene
                s["video_path"], s["_is_map"] = got[0], True
                s["image_paths"], s["credit"] = [got[1]["photo"]], [got[1]["credit"]]
        shots = len(s.get("image_paths") or [1])
        kind = (("map" if s.get("map") else "chain" if s.get("chain")
                 else "cast card" if s.get("dossier") else "graphic")
                if s.get("_is_map") else "b-roll") if s.get("video_path") \
            else f"{shots} shot(s)"
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
    punch_cues, chapter_titles = [], []
    # Where the music ducks and the sound design fires.
    card_starts, pivot_start = [], None
    # Set by the intro-card block below: overrides the NEXT chapter's own
    # chapter_marks start so its musical act (see plan_chapter_score) opens
    # under the card instead of under the tail of the previous chapter.
    # The on-screen chapter title and the music's "card lift" still land on
    # the real cursor once the card ends — only which act owns that stretch
    # of music changes.
    next_chapter_mark_start = None
    n_chapters = len(chapters)
    stamp_cues, rhythm_cues, silence = [], [], None
    scene_offsets = []
    prev_chapter_start = 0.0
    for pos, (c_idx, group) in enumerate(chapters):
        reel = os.path.join(build_dir, f"lf_reel_{c_idx:02d}.mp4")

        # The beat before the decision: the clock has been rising under the
        # end of the previous chapter, then one second of black and total
        # silence, then a low hit as this chapter opens. Inserted before the
        # chapter mark is taken, so the decision's own score starts on the
        # hit, not under the black.
        if group[0].get("is_pivot") and pos > 0:
            try:
                black = _build_black_clip(SILENCE_BEFORE_DECISION, build_dir)
                clock_start = max(prev_chapter_start, cursor - CLOCK_SPAN)
                silence = (clock_start, cursor, cursor + SILENCE_BEFORE_DECISION)
                rhythm_cues.append(("clock", clock_start, cursor - clock_start))
                reels.append(black)
                cursor += SILENCE_BEFORE_DECISION
                rhythm_cues.append(("hit", cursor))
                print(f"🕰️  Clock from {clock_start:.1f}s, silence at "
                      f"{silence[1]:.1f}s, hit at {cursor:.1f}s.")
            except Exception as e:
                silence = None
                print(f"⚠️ Silence beat skipped ({type(e).__name__}: {e}).")
        prev_chapter_start = cursor

        # The top-left date/place stamps are off (docs/VIDEO_SPEC.md 9): set
        # once per chapter, they contradicted the date ruler, which follows
        # every date the narrator says.
        stamp = (group[0].get("stamp") or "").strip() if STAMPS_ENABLED else ""
        if stamp:
            stamp_cues.append((cursor + 0.6, stamp))
            rhythm_cues.append(("keys", cursor + 0.6, len(stamp) * TYPE_RATE))
        print(f"🔗 Chapter {c_idx + 1}: crossfading {len(group)} scenes...")
        offs, total = _xfade_chapter([g["_clip"] for g in group],
                                     [g["duration"] + XFADE for g in group],
                                     reel, cut_index=cuts)
        if pos == 0 and group[0].get("_opening"):
            # the opening laid over the start of the first chapter's reel
            tmp = reel + ".open.mp4"
            try:
                _run(["ffmpeg", "-y", "-i", reel, "-i", group[0]["_opening"],
                      "-filter_complex", "[1:v]setpts=PTS-STARTPTS[o];"
                      "[0:v][o]overlay=0:0:eof_action=pass[v]",
                      "-map", "[v]", "-map", "0:a", "-c:v", "libx264", "-preset", "veryfast",
                      "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "copy", tmp])
                os.replace(tmp, reel)
            except Exception as e:
                group[0].pop("_ticks", None)
                print(f"⚠️ THEN / NOW opening not laid ({type(e).__name__}: {e}).")
        cuts += max(0, len(group) - 1)
        heading = group[0].get("chapter_heading", f"Part {c_idx + 1}")
        mark_start = cursor if next_chapter_mark_start is None else next_chapter_mark_start
        chapter_marks.append((heading, mark_start))
        next_chapter_mark_start = None
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
            scene_offsets.append((g, cursor + o))
            # where this scene starts in lf_merged.mp4 (longform_clips.py)
            g["_global_start"] = cursor + o
            front = (g.get("front_stamp") or "").strip() if STAMPS_ENABLED else ""
            if front:
                stamp_cues.append((cursor + o + 0.5, front))
                rhythm_cues.append(("keys", cursor + o + 0.5, len(front) * TYPE_RATE))
            if g.get("_ticks"):
                rhythm_cues.append(("file", cursor + o, g["_ticks"]))
            # a cast card's freeze: the native Short's card sound (owner,
            # 25 Oct: whoosh in, flash crack, a boom on the name, the stamp)
            if g.get("_cast_freeze") is not None:
                hit_at = cursor + o + g["_cast_freeze"]
                try:
                    from native_cast import card_sfx
                    lead = min(0.6, hit_at)
                    wav = card_sfx(os.path.join(build_dir, f"lf_cast_sfx_{len(rhythm_cues):03d}.wav"),
                                   lead, lead + 2.8, slam_after=0.2, stamp_after=0.45)
                    rhythm_cues.append(("file", hit_at - lead, wav))
                except Exception as e:
                    print(f"   ↩️  Cast card sound failed ({type(e).__name__}: {e}).")
                    rhythm_cues.append(("whoosh", max(0.0, hit_at - 0.3)))
                    rhythm_cues.append(("impact", hit_at))

        # A chapter's punch phrase, flashed over whatever frame is on screen
        # at the exact moment it is spoken. The phrase is attached to the
        # ONE scene that actually contains it (longform_llm.py tracks which
        # segment it was marked in), so this only has to search that scene's
        # own word timings — the phrase is a verbatim quote of them, so it
        # is expected to be found, not guessed at.
        phrase_scene = next((g for g in group if g.get("punch_phrase")), None)
        if phrase_scene:
            phrase = phrase_scene["punch_phrase"]
            scene_local = offs[group.index(phrase_scene)]
            found = _find_phrase_time(phrase_scene, phrase)
            if found:
                phrase_start, phrase_end = found
                punch_start = cursor + scene_local + phrase_start
                end_spoken = cursor + scene_local + phrase_end
                hold = max(PUNCH_HOLD, (end_spoken - punch_start) + PUNCH_TAIL)
                if (total > hold + 4
                        and punch_start + hold < cursor + total - 1.5):
                    punch_cues.append((punch_start, end_spoken, phrase))
            else:
                # Should not happen — the phrase is a verbatim quote of this
                # scene's own narration — but a TTS engine can occasionally
                # split or merge a word unexpectedly. No callout beats a
                # guessed one: that guess is exactly the bug this replaced.
                print(f"⚠️ Punch phrase {phrase!r} not found in its own "
                      f"scene's word timings (chapter {c_idx + 1}) — "
                      f"skipping the callout rather than guess its timing.")

        cursor += total
        reels.append(reel)

        # The cold-open card belongs right after the first chapter and
        # before the second names anyone. The first chapter can never be
        # dropped (generate_longform_script returns None outright if it
        # fails), so pos == 0 is always the real first chapter here.
        # Musically the card opens the next chapter rather than closing
        # this one: its score should be playing under the card already,
        # not waiting for the card to end, so the next chapter's
        # chapter_marks entry is pinned to right here instead of the
        # cursor once the card has played out.
        if pos == 0 and intro_epigraph:
            try:
                intro_clip, intro_duration = _build_intro_card(intro_epigraph, build_dir)
                reels.append(intro_clip)
                intro_start = cursor
                next_chapter_mark_start = intro_start
                cursor += intro_duration
                preview = " ".join(intro_epigraph.get("sentences")
                                    or [intro_epigraph.get("text") or ""])
                print(f"🖤 Intro card inserted at {intro_start:.1f}s "
                      f"({intro_duration:.1f}s, no narration — the next "
                      f"chapter's score starts here): {preview[:70]!r}")
            except Exception as e:
                print(f"⚠️ Intro card render failed "
                      f"({type(e).__name__}: {e}) — continuing without it.")

        # The pre-verdict card sits right before the LAST chapter — a
        # sad, weary pattern-interrupt after everything else has played
        # out. Same act-boundary trick as the opening card: pinned to the
        # closing chapter's own opening, not the tail of the chapter
        # before it. n_chapters >= 3 keeps this position distinct from
        # the opening card's (pos == 0); with exactly 2 chapters they
        # would be the same spot, so the card is skipped rather than
        # doubled up there.
        if pos == n_chapters - 2 and n_chapters >= 3 and verdict_epigraph:
            try:
                verdict_clip, verdict_duration = _build_intro_card(verdict_epigraph, build_dir,
                                                                      name="lf_verdict")
                reels.append(verdict_clip)
                verdict_start = cursor
                next_chapter_mark_start = verdict_start
                cursor += verdict_duration
                preview = " ".join(verdict_epigraph.get("sentences")
                                    or [verdict_epigraph.get("text") or ""])
                print(f"🖤 Pre-verdict card inserted at {verdict_start:.1f}s "
                      f"({verdict_duration:.1f}s, no narration — the "
                      f"closing chapter's score starts here): {preview[:70]!r}")
            except Exception as e:
                print(f"⚠️ Pre-verdict card render failed "
                      f"({type(e).__name__}: {e}) — continuing without it.")

    total_duration = cursor

    # ---- Date ticker: every date the narrator says, on the video's clock ----
    ticker_clips, lift_periods = [], []
    try:
        from date_ticker import collect, render_overlays
        era = next((sc.get("era_year") for sc, _ in scene_offsets if sc.get("era_year")), None)
        date_events = collect(scene_offsets, era_year=era)
        if date_events:
            ticker_clips, tk = render_overlays(date_events, build_dir)
            lift_periods = tk.periods()
            rhythm_cues.extend(tk.sound_cues())
            print(f"📅 Date ticker: {len(date_events)} date(s) — "
                  + ", ".join(f"{e['label']}{' ' + e['time'] if e['time'] else ''} "
                              f"@{e['at']:.0f}s" for e in date_events[:20]))
    except Exception as e:
        ticker_clips, lift_periods = [], []
        print(f"⚠️ Date ticker skipped ({type(e).__name__}: {e}).")

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
    generate_subtitles(usable, ass_path, global_offsets, punch_cues=punch_cues,
                       chapter_titles=chapter_titles,
                       total_duration=total_duration, stamp_cues=stamp_cues,
                       lift_periods=lift_periods)

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
    # Punch-phrase callouts only. There was a riser under every chapter card
    # too; the channel owner's verdict on the first published cut was that a
    # swoosh under sliding text reads as silly, and he is right — it is the
    # one piece of sound design that announces itself, on a channel whose
    # whole voice is that it does not. The impacts stay: they sit under a
    # phrase, they are felt rather than heard, and nothing was wrong with them.
    sfx_cues = [("impact", start) for start, _, _ in punch_cues] + rhythm_cues
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
        envelope = music_envelope(card_starts, pivot_start, total_duration,
                                  silence=silence)
        mix_parts.append(
            f"[{idx}:a]aloop=loop=-1:size=2e9,atrim=0:{total_duration:.3f},"
            f"volume=volume='{envelope}':eval=frame,"
            f"afade=t=out:st={max(total_duration - 2.5, 0):.2f}:d=2.5[bg]")
        mix_labels.append("[bg]")

    if sfx:
        idx = len(inputs) // 2
        inputs += ["-i", sfx]
        mix_labels.append(f"[{idx}:a]")

    # Date ticker clips ride on top of the graded picture, under the
    # captions; with them the video goes through the filter graph too.
    vmap = "0:v"
    vargs = ["-vf", vf]
    if ticker_clips:
        base_vf, ass_vf = vf.split(f"ass='", 1)
        chain = [f"[0:v]{base_vf.rstrip(',')}[tk0]"]
        for k, (start, clip) in enumerate(ticker_clips):
            idx = len(inputs) // 2
            inputs += ["-i", clip]
            chain.append(f"[{idx}:v]setpts=PTS-STARTPTS+{start:.3f}/TB[tc{k}]")
            chain.append(f"[tk{k}][tc{k}]overlay=0:{H - 200}:eof_action=pass[tk{k + 1}]")
        chain.append(f"[tk{len(ticker_clips)}]ass='{ass_vf}[vout]")
        mix_parts = chain + mix_parts
        vmap, vargs = "[vout]", []

    if len(mix_labels) > 1:
        # No separate muting needed for the intro card: the merged track's
        # own audio is already silent there (the card's clip carries a
        # silent stream — see _build_intro_card), and the score is meant
        # to be playing under it now (see the chapter_marks override
        # above, which puts the card inside "The Claim"'s act rather than
        # "The Cost"'s), not gated out.
        mix_parts.append(
            f"{''.join(mix_labels)}amix=inputs={len(mix_labels)}:"
            f"duration=first:normalize=0,"
            f"afade=t=out:st={max(total_duration - 1.2, 0):.2f}:d=1.2[aout]")
        cmd = ["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(mix_parts),
               "-map", vmap, "-map", "[aout]", *vargs,
               "-c:v", "libx264", "-preset", "medium", "-crf", "21",
               "-c:a", "aac", "-b:a", "192k", final_output]
    elif ticker_clips:
        cmd = ["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(mix_parts),
               "-map", vmap, "-map", "0:a",
               "-c:v", "libx264", "-preset", "medium", "-crf", "21",
               "-c:a", "copy", final_output]
    else:
        cmd = ["ffmpeg", "-y", "-i", merged, "-vf", vf,
               "-c:v", "libx264", "-preset", "medium", "-crf", "21",
               "-c:a", "copy", final_output]
    _run(cmd)

    print(f"✅ Long-form render complete: {final_output} "
          f"({total_duration / 60:.1f} min, {len(chapter_marks)} chapters)")
    return final_output, total_duration, chapter_marks, music_attribution
