"""
Before/after preview of the three rhythm changes, rendered from the
preview-assets branch (see fetch_preview_assets.py):

  1. a different look and cutting speed per act (warm/slow, red/tense,
     cold/fast),
  2. typewriter date-and-place stamps when the story moves,
  4. silence before the decision: a ticking bed, one second of black and
     total silence, then a hard hit as the order is spoken.

    python tools/render_rhythm_preview.py <assets_dir> <out.mp4>
"""

import json
import os
import subprocess
import sys

FFMPEG = os.getenv("FFMPEG", "ffmpeg")
W, H, FPS = 1280, 720, 30
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONTS_DIR = os.path.join(ROOT, "fonts")
MUSIC_DIR = os.path.join(ROOT, "music")

# Per-act grades. B&W archive photos take a tint well, so the three acts
# read as three different places even when every photo is monochrome.
GRADES = {
    "none": "",
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

GAP = 0.35          # breath between narration lines
SILENCE = 1.0       # the black, silent beat before the decision
TYPE_RATE = 0.055   # seconds per typed character
KEYCLICK_GAIN = 0.08       # was 0.22: present, never in the way of the voice
CLOCK_START_DB = -30       # the clock enters barely audible...
CLOCK_PEAK_DB = -6         # ...and is clearly there by the silence
MUSIC_UNDER_CLOCK = 0.3    # music slides down to 30% as the clock rises


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        sys.stderr.write(r.stderr[-3000:])
        raise SystemExit(f"ffmpeg failed: {' '.join(cmd[:6])}...")


def ass_time(t):
    h, rem = divmod(max(0.0, t), 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


ASS_HEAD = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Body,Anton,38,&H00FFFFFF,&H0000E8FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,1,2,90,90,46,1
Style: Stamp,FreeMono,26,&H00E8F0F0,&H00FFFFFF,&H00000000,&H64000000,1,0,0,0,100,100,2,0,1,1.4,0,7,52,52,44,1
Style: Slate,Anton,110,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,6,0,1,0,0,5,0,0,0,1
Style: Sub,FreeMono,26,&H00B0B0B0,&H00FFFFFF,&H00000000,&H00000000,1,0,0,0,100,100,2,0,1,0,0,5,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def captions(text, start, dur):
    """Four-word chunks timed by word share, like the real renderer."""
    words = text.split()
    chunks = [words[i:i + 4] for i in range(0, len(words), 4)]
    out, t = [], start
    for c in chunks:
        d = dur * len(c) / len(words)
        out.append(f"Dialogue: 1,{ass_time(t)},{ass_time(t + d)},Body,,0,0,0,,"
                   f"{' '.join(c).upper()}")
        t += d
    return out


def stamp(text, start, hold=2.6):
    """Typed in one character at a time with a block cursor, held, faded."""
    ev = []
    n = len(text)
    for i in range(1, n + 1):
        a, b = start + (i - 1) * TYPE_RATE, start + i * TYPE_RATE
        ev.append(f"Dialogue: 2,{ass_time(a)},{ass_time(b)},Stamp,,0,0,0,,"
                  f"{text[:i]}█")
    end_typed = start + n * TYPE_RATE
    ev.append(f"Dialogue: 2,{ass_time(end_typed)},{ass_time(end_typed + hold)},"
              f"Stamp,,0,0,0,,{{\\fad(0,500)}}{text}")
    return ev, n * TYPE_RATE


def shot(img, dur, grade, out, push=0.07, direction=1):
    """One Ken Burns shot, graded."""
    frames = max(1, int(round(dur * FPS)))
    z = f"1+{push}*on/{frames}" if direction > 0 else f"1+{push}-{push}*on/{frames}"
    vf = (f"scale=2560:1440:force_original_aspect_ratio=increase,crop=2560:1440,"
          f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
          f":d={frames}:s={W}x{H}:fps={FPS}")
    if GRADES[grade]:
        vf += "," + GRADES[grade]
    vf += ",format=yuv420p"
    run([FFMPEG, "-y", "-loglevel", "error", "-loop", "1", "-i", img,
         "-vf", vf, "-frames:v", str(frames), "-r", str(FPS),
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", out])


def black(dur, out):
    run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i",
         f"color=c=black:s={W}x{H}:r={FPS}:d={dur}", "-c:v", "libx264",
         "-preset", "veryfast", "-pix_fmt", "yuv420p", out])


def build(assets, plan, work, out_mp4, tag):
    """plan: list of segments. Each is a dict:
       {"kind": "line", "line": i, "images": [...], "grade": g, "push": p,
        "stamp": str|None, "tick": bool, "hit": bool, "hold": s}
       {"kind": "silence"}
    Returns nothing; writes out_mp4 (video + narration + music + sfx + subs).
    """
    man = json.load(open(os.path.join(assets, "manifest.json")))
    lines = man["lines"]
    clips, events, audio, t = [], [], [], 0.0
    tick_spans, hits, keyclicks, music_gaps = [], [], [], []

    for si, seg in enumerate(plan):
        if seg["kind"] == "silence":
            p = os.path.join(work, f"{tag}_{si}_black.mp4")
            black(SILENCE, p)
            clips.append(p)
            music_gaps.append((t, t + SILENCE))
            t += SILENCE
            continue
        ln = lines[seg["line"]]
        lead = seg.get("lead", 0.0)
        seg_dur = lead + ln["duration"] + seg.get("hold", 0.0) + GAP
        if seg.get("stamp"):
            ev, typed = stamp(seg["stamp"], t + 0.15)
            events += ev
            keyclicks.append((t + 0.15, typed))
        audio.append((os.path.join(assets, ln["file"]), t + lead))
        events += captions(ln["text"], t + lead, ln["duration"])
        if seg.get("tick"):
            tick_spans.append((t, t + seg_dur))
        if seg.get("hit"):
            hits.append(t)
        imgs = seg["images"]
        per = seg_dur / len(imgs)
        for k, img in enumerate(imgs):
            p = os.path.join(work, f"{tag}_{si}_{k}.mp4")
            shot(os.path.join(assets, img), per, seg["grade"], p,
                 push=seg.get("push", 0.07), direction=1 if k % 2 == 0 else -1)
            clips.append(p)
        t += seg_dur
    total = t

    # Video: hard cuts, concatenated.
    lst = os.path.join(work, f"{tag}_list.txt")
    with open(lst, "w") as f:
        f.writelines(f"file '{c}'\n" for c in clips)
    silent = os.path.join(work, f"{tag}_video.mp4")
    run([FFMPEG, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
         "-i", lst, "-c", "copy", silent])

    ass = os.path.join(work, f"{tag}.ass")
    with open(ass, "w", encoding="utf-8") as f:
        f.write(ASS_HEAD + "\n".join(events) + "\n")

    # Audio: narration + music (ducked out under ticking and the silence,
    # a second track after the hit) + synthesised sfx.
    inputs, filt, mix = ["-i", silent], [], []
    for i, (path, at) in enumerate(audio):
        inputs += ["-i", path]
        d = int(at * 1000)
        filt.append(f"[{i + 1}:a]aformat=channel_layouts=stereo,adelay={d}|{d},"
                    f"volume=1.0[n{i}]")
        mix.append(f"[n{i}]")
    base = len(audio) + 1
    tracks = seg_music = plan_music(plan, hits, total)
    for j, (track, start, end) in enumerate(tracks):
        inputs += ["-stream_loop", "-1", "-i", os.path.join(MUSIC_DIR, track)]
        d = int(start * 1000)
        dur = end - start
        vol = 0.16
        gate = ""
        for a, b in music_gaps:
            if b > start and a < end:
                gate += (f",volume=enable='between(t,{a - start:.2f},{b - start:.2f})'"
                         f":volume=0")
        for a, b in tick_spans:
            if b > start and a < end:
                # Linear slide from full to MUSIC_UNDER_CLOCK across the
                # build-up, and held there to the end of the span.
                a0, b0 = a - start, b - start
                gate += (f",volume=eval=frame:volume='if(lt(t,{a0:.2f}),1,"
                         f"if(gt(t,{b0:.2f}),{MUSIC_UNDER_CLOCK},"
                         f"1-(1-{MUSIC_UNDER_CLOCK})*(t-{a0:.2f})/{b0 - a0:.2f}))'")
        filt.append(f"[{base + j}:a]atrim=0:{dur:.2f},asetpts=PTS-STARTPTS,"
                    f"afade=t=in:d=1.2,afade=t=out:st={max(0, dur - 1.0):.2f}:d=1.0,"
                    f"volume={vol}{gate},aformat=channel_layouts=stereo,"
                    f"adelay={d}|{d}[m{j}]")
        mix.append(f"[m{j}]")
    k = 0
    for a, b in tick_spans:
        span = b - a
        # A mechanical tick: a very short noise transient plus two quickly
        # damped metallic partials, once a second.
        expr = ("((random(0)*2-1)*exp(-900*mod(t,1))*0.9"
                "+sin(2*PI*2750*t)*exp(-160*mod(t,1))*0.55"
                "+sin(2*PI*5600*t)*exp(-260*mod(t,1))*0.25)")
        ramp = (f"pow(10,({CLOCK_START_DB}+({CLOCK_PEAK_DB}-({CLOCK_START_DB}))"
                f"*t/{span:.2f})/20)")
        d = int(a * 1000)
        filt.append(f"aevalsrc='{expr}':s=44100:d={span + SILENCE / 2:.2f},"
                    f"highpass=f=500,lowpass=f=8000,"
                    f"aecho=0.8:0.88:170|340|560|820:0.5|0.34|0.22|0.12,"
                    f"volume=eval=frame:volume='{ramp}',"
                    # The last echo rings into the silence and is gone
                    # by its midpoint, so the hit still lands on nothing.
                    f"afade=t=out:st={span:.2f}:d={SILENCE / 2:.2f},"
                    f"aformat=channel_layouts=stereo,adelay={d}|{d}[s{k}]")
        mix.append(f"[s{k}]"); k += 1
    for a in hits:
        expr = ("(sin(2*PI*48*t)*0.9+sin(2*PI*96*t)*0.35"
                "+(random(0)*2-1)*exp(-22*t)*0.8)*exp(-2.2*t)")
        d = int(a * 1000)
        filt.append(f"aevalsrc='{expr}':s=44100:d=2.2,volume=0.45,"
                    f"aformat=channel_layouts=stereo,adelay={d}|{d}[s{k}]")
        mix.append(f"[s{k}]"); k += 1
    for a, dur in keyclicks:
        expr = f"(random(0)*2-1)*exp(-140*mod(t,{TYPE_RATE}))*{KEYCLICK_GAIN}"
        d = int(a * 1000)
        filt.append(f"aevalsrc='{expr}':s=44100:d={dur:.2f},highpass=f=1500,"
                    f"aformat=channel_layouts=stereo,adelay={d}|{d}[s{k}]")
        mix.append(f"[s{k}]"); k += 1
    filt.append("".join(mix) + f"amix=inputs={len(mix)}:normalize=0,"
                f"atrim=0:{total:.2f},alimiter=limit=0.95[aout]")
    fonts = FONTS_DIR.replace(":", r"\:")
    run([FFMPEG, "-y", "-loglevel", "error", *inputs,
         "-filter_complex", ";".join(filt),
         "-vf", f"ass={ass}:fontsdir={fonts}",
         "-map", "0:v", "-map", "[aout]", "-c:v", "libx264", "-preset",
         "veryfast", "-crf", "21", "-c:a", "aac", "-b:a", "160k",
         "-t", f"{total:.2f}", out_mp4])
    return total


def plan_music(plan, hits, total):
    """One track until the hit, a different one after it (the act changes);
    a single continuous track when there is no hit."""
    a = "A Singular Perversion - Darkness - Kevin MacLeod.mp3"
    b = "Crossing the Threshold - Ghostpocalypse - Kevin MacLeod.mp3"
    if not hits:
        return [(a, 0.0, total)]
    return [(a, 0.0, hits[0] - SILENCE), (b, hits[0] + 0.6, total)]


def slate(text, sub, dur, work, out):
    ass = os.path.join(work, f"slate_{text}.ass")
    with open(ass, "w", encoding="utf-8") as f:
        f.write(ASS_HEAD
                + f"Dialogue: 0,0:00:00.00,{ass_time(dur)},Slate,,0,0,0,,"
                  f"{{\\fad(250,250)\\pos({W // 2},{H // 2 - 30})}}{text}\n"
                + f"Dialogue: 0,0:00:00.00,{ass_time(dur)},Sub,,0,0,0,,"
                  f"{{\\fad(250,250)\\pos({W // 2},{H // 2 + 60})}}{sub}\n")
    fonts = FONTS_DIR.replace(":", r"\:")
    run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i",
         f"color=c=black:s={W}x{H}:r={FPS}:d={dur}", "-f", "lavfi", "-i",
         f"anullsrc=r=44100:cl=stereo", "-vf", f"ass={ass}:fontsdir={fonts}",
         "-t", str(dur), "-c:v", "libx264", "-preset", "veryfast",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", out])


def main(assets, out_mp4):
    work = os.path.join(os.path.dirname(os.path.abspath(out_mp4)), "work")
    os.makedirs(work, exist_ok=True)
    imgs = [i["file"] for i in json.load(
        open(os.path.join(assets, "manifest.json")))["images"]]
    pick = json.loads(os.getenv("PREVIEW_PICK", "null")) or {}

    def I(*keys):
        return [imgs[k] for k in keys]

    P = pick or {"a0": [0, 1], "a1": [2, 3], "b2": [4, 5], "b3": [6],
                 "c4": [7, 8, 9], "c5": [10, 11, 12]}

    before = [
        {"kind": "line", "line": i, "images": I(*P[key]), "grade": "none",
         "push": 0.06}
        for i, key in enumerate(["a0", "a1", "b2", "b3", "c4", "c5"])
    ]
    after = [
        # Act 1 — the plan. Warm, faded, slow: long holds, gentle push.
        {"kind": "line", "line": 0, "images": I(P["a0"][0]), "grade": "warm",
         "push": 0.05, "stamp": "BEIJING · SUMMER 1958", "lead": 0.6},
        {"kind": "line", "line": 1, "images": I(P["a1"][0]), "grade": "warm",
         "push": 0.05},
        # Act 2 — the decision. Red, tense, a clock ticking under it.
        {"kind": "line", "line": 2, "images": I(*P["b2"]), "grade": "red",
         "push": 0.10, "tick": True, "lead": 0.5, "hold": 0.8,
         "stamp": "FUJIAN COAST · 23 AUGUST 1958"},
        {"kind": "silence"},
        {"kind": "line", "line": 3, "images": I(*P["b3"]), "grade": "red",
         "push": 0.03, "hit": True, "lead": 0.35, "hold": 1.4},
        # Act 3 — the cost. Cold, fast hard cuts.
        {"kind": "line", "line": 4, "images": I(*P["c4"]), "grade": "cold",
         "push": 0.12, "stamp": "KINMEN · THE SAME NIGHT"},
        {"kind": "line", "line": 5, "images": I(*P["c5"]), "grade": "cold",
         "push": 0.12, "hold": 0.6},
    ]
    parts = []
    s1 = os.path.join(work, "slate_before.mp4")
    slate("BEFORE", "ONE RHYTHM THROUGHOUT", 2.0, work, s1)
    b = os.path.join(work, "before.mp4")
    build(assets, before, work, b, "before")
    s2 = os.path.join(work, "slate_after.mp4")
    slate("AFTER", "LOOK · STAMPS · SILENCE", 2.0, work, s2)
    a = os.path.join(work, "after.mp4")
    build(assets, after, work, a, "after")
    lst = os.path.join(work, "final.txt")
    with open(lst, "w") as f:
        for p in (s1, b, s2, a):
            f.write(f"file '{p}'\n")
    run([FFMPEG, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
         "-i", lst, "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
         "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", out_mp4])
    print(f"wrote {out_mp4}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
