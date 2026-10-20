"""
Shorts cut from the long-form video, each pointing back at it.

WHY
Every long-form run ends with a verified story (the fact sheet), real
footage, cast cards and a teaser written to hook a stranger. The Shorts lane
never used any of it, and nothing in either lane sent a viewer from one to
the other. Cutting two Shorts out of each long-form video costs no writing
and no image generation, repeats nothing a model could get wrong, and gives
every long-form video a way in from the Shorts feed.

WHAT IS CUT
Up to LONGFORM_CLIPS clips of CLIP_MIN-CLIP_MAX seconds, each made of whole
scenes (so each starts and ends on a sentence) inside ONE chapter (the
timeline between chapters carries title cards and black):
  1. the teaser, chapter 1: the big picture, ending on the video's question,
     opened by the news hook (below);
  2. the main event, the pivot chapter, from its first line that is not a
     bridge back in time ("Meanwhile...", "Let's go back...");
  3. then the chapter after the pivot, the same way.

HOW
The source is lf_merged.mp4, the picture and narration before the long-form
captions and music are burned in. The 16:9 picture is trimmed to 3:2 and set
across the middle of a 9:16 frame over a blurred copy of itself; the Shorts'
own big captions (video_engine.generate_subtitles, with its yellow figures)
go below it, the video's headline above it, a red "FULL STORY ON THE
CHANNEL" bar between the two for the whole clip, and "WATCH THE FULL VIDEO"
takes the headline's place for the last seconds.

WHERE THEY GO
Uploaded with the long-form lane's own privacy (private on both lanes, so
the owner publishes them with the video they link to), with the full video's
link in the description and as the first comment. Every clip's description
carries CLIP_MARKER, which is how the daily Shorts lane tells a clip from one
of its own episodes (published_history.is_clip): a clip must not make the
daily run stand down, and it carries no "Next file" promise.
"""

import copy
import os
import re
import subprocess

import story_mode as sm

LONGFORM_CLIPS = int(os.getenv("LONGFORM_CLIPS", "0"))
CLIP_MIN = 25.0
CLIP_MAX = 58.0
CLIP_TAIL = 0.35                  # after the last word, before the cut
CTA_SECONDS = 4.0
CLIP_CTA = "FULL STORY ON THE CHANNEL"
CLIP_MARKER = "From the full documentary:"
MUSIC_LEVEL = 0.08

W, H, FPS = 1080, 1920, 30
FG_W, FG_H = 1080, 720            # the 16:9 picture, trimmed to 3:2


# ---------------------------------------------------------------------------
# Choosing the clips
# ---------------------------------------------------------------------------

def _opens_with_bridge(scene) -> bool:
    first = re.split(r"(?<=[.!?])\s+", (scene.get("narration") or "").strip())[0]
    low = first.lower()
    return any(w in low for w in sm.BRIDGE_WORDS)


def _span(chapter_scenes, start):
    """Whole scenes from `start`, as many as fit in CLIP_MAX, or None when
    the result is shorter than CLIP_MIN."""
    first = chapter_scenes[start]
    t0 = first["_global_start"]
    picked = []
    for s in chapter_scenes[start:]:
        if s["_global_start"] + s["duration"] - t0 > CLIP_MAX:
            break
        picked.append(s)
    if not picked:
        return None
    length = picked[-1]["_global_start"] + picked[-1]["duration"] - t0
    return picked if length >= CLIP_MIN else None


def pick_clips(scenes, limit):
    """[(label, [scene, ...])] — at most `limit` clips, in the order above."""
    timed = [s for s in scenes if s.get("_global_start") is not None
             and s.get("duration")]
    chapters = {}
    for s in timed:
        chapters.setdefault(s.get("chapter_index", 0), []).append(s)
    order = sorted(chapters)
    if not order:
        return []

    candidates = [("teaser", order[0], False)]
    pivot = next((c for c in order if chapters[c][0].get("is_pivot")), None)
    if pivot is not None:
        candidates.append(("main event", pivot, True))
        later = [c for c in order if c > pivot]
        if later:
            candidates.append(("aftermath", later[0], True))
    # More clips (owner, 2026-10-09: clips bring ~85% of the Shorts views):
    # the other story chapters, in order, never the closing verdict.
    named = {c for _, c, _ in candidates}
    for c in order[1:-1]:
        if c not in named:
            candidates.append(("chapter", c, True))

    clips, used = [], set()
    for label, c_idx, skip_bridge in candidates:
        if len(clips) >= limit or c_idx in used:
            continue
        group = chapters[c_idx]
        start = 0
        if skip_bridge:
            while start < len(group) - 1 and _opens_with_bridge(group[start]):
                start += 1
        span = _span(group, start)
        if span:
            clips.append((label, span))
            used.add(c_idx)
        else:
            print(f"   ✂️  No {label} clip: chapter {c_idx + 1} has no "
                  f"{CLIP_MIN:.0f}-{CLIP_MAX:.0f}s run of whole scenes.")
    return clips


# ---------------------------------------------------------------------------
# The teaser's news hook
# ---------------------------------------------------------------------------

# A third of the Houthi teaser's viewers left between 0:03 and 0:06 (owner's
# Studio, 20 Oct): chapter 1 opens on background ("Twelve founding members
# created NATO in 1949..."), not on the news the viewer swiped in for. The
# teaser now opens on the video's own title, spoken, over the story's
# strongest picture, and only then goes back in time. The title is the
# packaging already checked against the headlines, so nothing new is claimed.
HOOK_PAD = 0.35                   # after the hook's last word

_STRONG = re.compile(r"\d|\b(never|first|last|dead|killed|died|executed|"
                     r"destroyed|million|thousand|war|combat|troops|invaded|"
                     r"erupted|collapsed|attack|bomb|massacre|revolt|fell|"
                     r"surrender|missile|strike|fire)", re.I)


def _hook_picture(scenes, seconds):
    """The scene whose picture opens the teaser: one on screen at least
    `seconds` (so the hook never runs into a chapter card), real photos and
    footage first, then the line that lands hardest, from the main event or
    the teaser chapter (never the closing verdict)."""
    pool = [s for s in scenes if s.get("_global_start") is not None
            and s.get("chapter_role") != "close"
            and not s.get("dossier") and not s.get("_is_map")]
    first = min((s.get("chapter_index", 0) for s in pool), default=0)
    pool = [s for s in pool if s.get("is_pivot")
            or s.get("chapter_index", 0) == first]
    fits = [s for s in pool if s.get("duration", 0) >= seconds]
    if not fits:
        return max(pool, key=lambda s: s.get("duration", 0), default=None)

    def key(s):
        real = bool(s.get("is_real") or s.get("video_path"))
        return (real, len(_STRONG.findall(s.get("narration") or "")))
    return max(fits, key=key)


def make_hook(script, scenes, build_dir):
    """{"text", "audio", "words", "length", "start"} or None: the title,
    voiced, and where its picture sits in lf_merged.mp4."""
    from audio_engine import _synthesize, get_audio_duration
    text = re.sub(r"\s+", " ", (script.get("title") or "").strip())
    if not text:
        return None
    audio = os.path.join(build_dir, "lf_clip_hook.mp3")
    try:
        words = _synthesize(text, audio)
        length = get_audio_duration(audio) + HOOK_PAD
    except Exception as e:
        print(f"⚠️ Teaser hook not voiced ({type(e).__name__}: {e}) — the "
              "teaser opens on chapter 1 as before.")
        return None
    pic = _hook_picture(scenes, length)
    if not pic:
        return None
    print(f"🪝 Teaser hook ({length:.1f}s): {text!r} over scene "
          f"{pic.get('narration', '')[:60]!r}")
    return {"text": text, "audio": audio, "words": words, "length": length,
            "start": pic["_global_start"]}


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _wrap(text, width=18):
    words, lines, line = (text or "").split(), [], ""
    for w in words:
        if line and len(line) + 1 + len(w) > width:
            lines.append(line)
            line = w
        else:
            line = f"{line} {w}".strip()
    if line:
        lines.append(line)
    return r"\N".join(lines[:3])


# The way to the full video stays on screen the whole clip: with only the
# last 4 seconds, four clips drew ~2,000 views and the video they cut from 7
# (10 Oct). A red bar just above the picture, in the thumbnails' red.
BAR_Y = 520                       # bar centre; the picture starts at 600
BAR_SIZE = 76
BAR_RED = "&H2028D6&"             # thumb_variants.RED (214, 40, 32), as BGR
CLIP_END = "WATCH THE FULL VIDEO"


def _cta_bar(length):
    from video_engine import FONTS_DIR, _ass_time
    try:
        from PIL import ImageFont
        font = ImageFont.truetype(os.path.join(FONTS_DIR, "Anton-Regular.ttf"), BAR_SIZE)
        # libass draws Anton at about two thirds of PIL's size for the
        # same number (measured on a rendered frame)
        text_w = font.getlength(CLIP_CTA) * 0.68
    except Exception:
        text_w = len(CLIP_CTA) * BAR_SIZE * 0.33
    bw, bh = int(text_w + 64), int(BAR_SIZE * 1.15)
    x0, y0 = 540 - bw // 2, BAR_Y - bh // 2
    span = f"{_ass_time(0)},{_ass_time(length)}"
    return [
        f"Dialogue: 3,{span},Cta,,0,0,0,,{{\\an7\\pos({x0},{y0})\\bord0\\shad0"
        f"\\1c{BAR_RED}\\fad(250,0)\\p1}}m 0 0 l {bw} 0 {bw} {bh} 0 {bh}{{\\p0}}",
        f"Dialogue: 4,{span},Cta,,0,0,0,,{{\\an5\\pos(540,{BAR_Y})\\fs{BAR_SIZE}"
        f"\\bord0\\shad0\\1c&H00FFFFFF&\\fad(250,0)}}{CLIP_CTA}",
    ]


def _headline_events(headline, length):
    from video_engine import _ass_time
    head = _wrap(headline.upper().replace("{", "").replace("}", ""))
    cta_at = max(0.0, length - CTA_SECONDS)
    style = r"{\an8\pos(540,250)\fs92\bord6\shad3\fad(250,200)"
    return [
        f"Dialogue: 3,{_ass_time(0)},{_ass_time(cta_at)},Cta,,0,0,0,,"
        f"{style}\\c&H00FFFFFF&}}{head}",
        f"Dialogue: 3,{_ass_time(cta_at)},{_ass_time(length)},Cta,,0,0,0,,"
        f"{style}}}{_wrap(CLIP_END, 22)}",
    ] + _cta_bar(length)


def render_clip(merged, clip_scenes, headline, out_path, build_dir, k, hook=None):
    """One vertical clip, opened by `hook` (make_hook) when given. Returns
    (path, length, music_attribution)."""
    from music_attribution import attribution_for
    from video_engine import FONTS_DIR, _pick_music, generate_subtitles

    t0 = clip_scenes[0]["_global_start"]
    body = (clip_scenes[-1]["_global_start"] + clip_scenes[-1]["duration"]
            - t0 + CLIP_TAIL)
    lead = hook["length"] if hook else 0.0
    length = lead + body

    # The clip's own captions: the scenes' word timings, moved onto the
    # clip's timeline. Copies, so nothing leaks back into the long-form data.
    scenes = [copy.deepcopy({k2: v for k2, v in s.items() if not k2.startswith("_")})
              for s in clip_scenes]
    offsets = [lead + s["_global_start"] - t0 for s in clip_scenes]
    if hook:
        scenes.insert(0, {"narration": hook["text"], "words": hook["words"],
                          "duration": hook["length"]})
        offsets.insert(0, 0.0)
    ass_path = os.path.join(build_dir, f"lf_clip_{k}.ass")
    generate_subtitles({"scenes": scenes}, ass_path, timeline_offsets=offsets,
                       total_duration=length, cta_text="")
    with open(ass_path, "a", encoding="utf-8") as f:
        f.write("\n" + "\n".join(_headline_events(headline, length)))

    safe_ass = os.path.abspath(ass_path).replace("\\", "/").replace(":", "\\:")
    safe_fonts = os.path.abspath(FONTS_DIR).replace("\\", "/").replace(":", "\\:")
    # the hook, then the teaser, joined into one 16:9 source ([src]/[srca])
    inputs = ["-ss", f"{t0:.3f}", "-t", f"{body:.3f}", "-i", merged]
    if hook:
        inputs += ["-ss", f"{hook['start']:.3f}", "-t", f"{lead:.3f}", "-i", merged,
                   "-i", hook["audio"]]
        join = (f"[1:v]setpts=PTS-STARTPTS[hv];"
                f"[2:a]apad,atrim=0:{lead:.3f},asetpts=PTS-STARTPTS[ha];"
                f"[0:v]setpts=PTS-STARTPTS[tv];[0:a]asetpts=PTS-STARTPTS[ta];"
                f"[hv][ha][tv][ta]concat=n=2:v=1:a=1[src][srca];")
        music_in = 3
    else:
        join = "[0:v]null[src];[0:a]anull[srca];"
        music_in = 1
    video = (
        f"{join}[src]split[a][b];"
        f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
        f"boxblur=28:2,eq=brightness=-0.22[bg];"
        f"[b]crop=ih*3/2:ih,scale={FG_W}:{FG_H}:flags=lanczos[fg];"
        f"[bg][fg]overlay=0:(H-h)/2,setsar=1,"
        f"ass='{safe_ass}':fontsdir='{safe_fonts}',"
        f"fade=t=out:st={max(length - 0.5, 0):.2f}:d=0.5[v]"
    )
    music = _pick_music()
    if music:
        inputs += ["-i", music]
        audio = (f"[{music_in}:a]aloop=loop=-1:size=2e9,atrim=0:{length:.3f},"
                 f"volume={MUSIC_LEVEL},afade=t=in:d=0.6,"
                 f"afade=t=out:st={max(length - 1.2, 0):.2f}:d=1.2[bg_a];"
                 f"[srca][bg_a]amix=inputs=2:duration=first:normalize=0,"
                 f"afade=t=out:st={max(length - 0.5, 0):.2f}:d=0.5[aout]")
    else:
        audio = f"[srca]afade=t=out:st={max(length - 0.5, 0):.2f}:d=0.5[aout]"
    cmd = ["ffmpeg", "-y", *inputs,
           "-filter_complex", f"{video};{audio}",
           "-map", "[v]", "-map", "[aout]", "-r", str(FPS),
           "-c:v", "libx264", "-preset", "medium", "-crf", "20",
           "-c:a", "aac", "-b:a", "192k", "-pix_fmt", "yuv420p",
           "-t", f"{length:.3f}", out_path]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
                   check=True)
    return out_path, length, attribution_for(music) if music else ""


def make_clips(scenes, script, build_dir="build", limit=None):
    """Renders the clips. Returns [{"path", "label", "scenes", "length",
    "music_attribution"}]. Never raises: the long-form video is the run's
    deliverable, and a failed clip must not cost it."""
    limit = LONGFORM_CLIPS if limit is None else limit
    merged = os.path.join(build_dir, "lf_merged.mp4")
    if limit <= 0 or not os.path.exists(merged):
        return []
    headline = (script.get("thumbnail_text") or script.get("title") or "").strip()
    out = []
    for k, (label, clip_scenes) in enumerate(pick_clips(scenes, limit)):
        path = os.path.join(build_dir, f"lf_clip_{k}.mp4")
        hook = make_hook(script, scenes, build_dir) if label == "teaser" else None
        try:
            _, length, music_credit = render_clip(merged, clip_scenes, headline,
                                                  path, build_dir, k, hook)
        except Exception as e:
            print(f"⚠️ Clip {k + 1} ({label}) failed ({type(e).__name__}: {e}).")
            continue
        print(f"✂️  Clip {k + 1}: {label}, {length:.1f}s — {path}")
        out.append({"path": path, "label": label, "scenes": clip_scenes,
                    "length": length, "music_attribution": music_credit})
    return out


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

def clip_metadata(clip, script, video_id, credit_block=""):
    """(title, description, first_comment) for one clip."""
    base = (script.get("title") or script.get("topic") or "").strip()
    heading = (clip["scenes"][0].get("chapter_heading") or "").strip()
    # The teaser carries the video's own title. Any other clip leads with its
    # chapter and names the story after it: "The First Morning — The Six-Day
    # War", using the part of a two-part title before its colon.
    subject = base.split(":")[0].strip() if ":" in base else base
    if clip["label"] == "teaser" or not heading or heading.lower() in base.lower():
        story = base
    else:
        story = f"{heading} — {subject}"
    suffix = " #shorts"
    story = re.sub(r"[<>]", "", story)
    if len(story) > 100 - len(suffix):
        story = story[:100 - len(suffix)].rsplit(" ", 1)[0].rstrip(".,;:")
    title = story + suffix

    link = f"https://youtu.be/{video_id}" if video_id else "(link added on upload)"
    parts = [f"{CLIP_MARKER} {base}\nWatch the whole story: {link}"]
    if script.get("description"):
        parts.append(re.sub(r"(^|\s)#\w+", "", script["description"]).strip())
    source = script.get("source_article") or {}
    if source.get("url"):
        parts.append(f"Source: \"{source.get('title', '')}\", Wikipedia — "
                     f"{source['url']}")
    if credit_block:
        parts.append(credit_block)
    if clip.get("music_attribution"):
        parts.append(clip["music_attribution"])
    parts.append("#shorts #history #documentary")
    description = "\n\n".join(p for p in parts if p)[:4900]
    comment = (f"The full story, start to finish: {link}" if video_id else "")
    return title, description, comment
