"""
A NOW / THEN Short that sends people to the long video (owner, 29-30 Oct:
the Shorts are the channel's only audience; each one ends on the full
video, "like the failure-point Short but more of the channel's theme").

  - the opening: today full screen, then today slides up into the top half
    while the past slides in from below, both centred, the counter rolls
    to the past's year on the seam, and the same past slides on over the
    whole frame (the long video's opening, turned for the phone)
  - then one real picture per line, each slowly pushing in, dissolving
    into the next; the line's year in a tag; the words under the picture
  - the end: the file closes: today slides down from the top, the past up
    from the bottom, they meet on an orange seam, THE FULL STORY lands on
    it and a pointer bounces towards the related-video link

    python tools/paradox_short.py owner_media/<story> out_dir

Reads <story>/short.json and the folder's now / then pictures
(owner_media.py). Writes out_dir/short.mp4 and out_dir/short.json.
"""

import io
import json
import os
import re
import subprocess
import sys

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import native_short as ns  # noqa: E402
import owner_media  # noqa: E402

SW, SH, FPS = 1080, 1920, 30
GAP = 0.25                    # between lines
LEAD = 0.3                    # before the first word
XF = 0.45                     # the dissolve between pictures
SPLIT_IN, ROLL, SLIDE = 0.8, 2.4, 0.9
CARD_HOLD = 2.8               # after the last word, the full story's card
ORANGE = (255, 150, 40)
NOW_FOCUS, THEN_FOCUS = (0.48, 0.30), (0.44, 0.60)
ANTON = os.path.join(HERE, "..", "fonts", "Anton-Regular.ttf")


def run(cmd):
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)


def font(size):
    from PIL import ImageFont
    return ImageFont.truetype(ANTON, size)


# ---------------------------------------------------------------- pictures

def fetch(url):
    """The picture at url. A Special:FilePath link (refused with 400 for
    some names) is resolved through the Commons API to the file's own url."""
    from urllib.parse import unquote
    from archive_engine import COMMONS_API, _polite_get
    if "Special:FilePath/" in url:
        name = unquote(url.split("Special:FilePath/", 1)[1]).replace("_", " ")
        r = _polite_get(COMMONS_API, params={"action": "query", "format": "json",
                                             "titles": f"File:{name}", "prop": "imageinfo",
                                             "iiprop": "url", "iiurlwidth": 2000}, timeout=30)
        r.raise_for_status()
        page = next(iter((r.json().get("query") or {}).get("pages", {}).values()), {})
        info = (page.get("imageinfo") or [{}])[0]
        url = info.get("thumburl") or info.get("url")
        if not url:
            raise SystemExit(f"❌ No Commons file named {name!r}")
    r = _polite_get(url)
    r.raise_for_status()
    return Image.open(io.BytesIO(r.content)).convert("RGB")


def cover(img, focus, w=SW, h=SH, zoom=1.0, at=(0.5, 0.42)):
    """w x h cut from the picture around its subject (focus, fractions),
    the subject placed at `at` of the frame."""
    s = max(w / img.width, h / img.height) * zoom
    big = img.resize((int(img.width * s) + 1, int(img.height * s) + 1), Image.LANCZOS)
    x = min(max(0, int(focus[0] * big.width - at[0] * w)), big.width - w)
    y = min(max(0, int(focus[1] * big.height - at[1] * h)), big.height - h)
    return big.crop((x, y, x + w, y + h))


def band(img, focus, zoom=1.0):
    """A wide picture as a tall frame: its subject in a band across the
    middle, over a blurred, darkened copy of itself (no amputated faces)."""
    bg = cover(img, focus).filter(ImageFilter.GaussianBlur(40))
    bg = ImageEnhance.Brightness(bg).enhance(0.4)
    side = int(min(img.width, img.height * 1.25) / zoom)
    x = min(max(0, int(focus[0] * img.width - side / 2)), img.width - side)
    fh = min(img.height, int(side * 0.9))
    y = min(max(0, int(focus[1] * img.height - fh / 2)), img.height - fh)
    fg = img.crop((x, y, x + side, y + fh)).resize((SW, int(SW * fh / side)), Image.LANCZOS)
    bg.paste(fg, (0, (SH - fg.height) // 2))
    return bg


def layer_for(img, focus, zoom_kind=None):
    if img.width / img.height <= 0.9:
        return cover(img, focus)
    if zoom_kind == "tight":
        return band(img, focus, zoom=2.2)
    return band(img, focus, zoom=1.4)


def grade_then(img):
    g = ImageEnhance.Contrast(img.convert("L").convert("RGB")).enhance(1.35)
    return g


# ---------------------------------------------------------------- overlays

def caption_png(text, path):
    f = font(96)
    w = int(f.getlength(text)) + 40
    img = Image.new("RGBA", (max(w, 10), 150), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((20, 10), text, font=f, fill=(255, 255, 255, 255),
                             stroke_width=9, stroke_fill=(0, 0, 0, 255))
    img.save(path)
    return img.width


def chunks(words, offset):
    """The narration in two- or three-word pieces with their times."""
    out, cur = [], []
    for w in words:
        cur.append(w)
        text = " ".join(x["word"] for x in cur)
        if len(cur) >= 3 or re.search(r"[.,;:!?]$", w["word"]) or len(text) > 16:
            out.append((text, offset + cur[0]["start"], offset + cur[-1]["end"]))
            cur = []
    if cur:
        out.append((" ".join(x["word"] for x in cur), offset + cur[0]["start"],
                    offset + cur[-1]["end"]))
    # each piece stays until the next one starts
    return [(t, s, out[i + 1][1] if i + 1 < len(out) else e + 0.3)
            for i, (t, s, e) in enumerate(out)]


# ---------------------------------------------------------------- clips

def still_clip(layer_png, seconds, out, tag_png=None, push=0.09):
    n = max(1, round(seconds * FPS))
    vf = (f"[0:v]scale={SW * 2}:{SH * 2},zoompan=z='1+{push}*on/{n}':"
          f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={SW}x{SH}:fps={FPS}[v0]")
    ins = ["-loop", "1", "-framerate", str(FPS), "-i", layer_png]
    if tag_png:
        ins += ["-loop", "1", "-framerate", str(FPS), "-i", tag_png]
        vf += ";[v0][1:v]overlay=48:150[v1]"
        last = "v1"
    else:
        last = "v0"
    run(["ffmpeg", "-y", *ins, "-filter_complex", vf + f";[{last}]format=yuv420p[v]",
         "-map", "[v]", "-frames:v", str(n), "-r", str(FPS),
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", out])


def opening_clip(now_png, then_png, split_at, take_at, seconds, then_year, work, out):
    """Today full, today up into the top half and the past in from below,
    the counter on the seam, then the same past over the whole frame."""
    import datetime as dt
    now_year = dt.date.today().year
    roll_at = split_at + SPLIT_IN
    frames = max(1, round(seconds * FPS))
    mask = os.path.join(work, "mask_v.png")
    m = Image.new("L", (SW, 2 * SH), 0)
    m.paste(255, (0, SH, SW, 2 * SH))
    m.save(mask)
    nowtag, thentag = os.path.join(work, "tag_now.png"), os.path.join(work, "tag_then.png")
    ns._tag(f"NOW · {now_year}").save(nowtag)
    ns._tag(f"THEN · {then_year}").save(thentag)
    pattern, _, changes = ns._counter_frames(now_year, then_year, os.path.join(work, "nixie"),
                                             roll=ROLL)
    cw = ns._nixie(then_year).size
    ticks = ns._ticks([roll_at + c for c in changes], os.path.join(work, "ticks.wav"), roll=ROLL)

    def ease(start, length):
        p = f"clip((t-{start:.3f})/{length:.3f},0,1)"
        return f"({p}*{p}*(3-2*{p}))"
    b = f"({SH}-960*{ease(split_at, SPLIT_IN)}-960*{ease(take_at, SLIDE)})"
    in_take = f"gte(t,{take_at:.3f})"
    y_now = f"if({in_take},{b}-1440,({b}-1920)/2)"
    y_then = f"if({in_take},{b}/2,{b}-480)"
    m_e = f"if({in_take},{b}/2,480)"

    def fade(lab, out_lab, fin=None, fout=None, d=0.4):
        f = f"[{lab}]format=rgba"
        if fin is not None:
            f += f",fade=t=in:st={fin:.3f}:d={d}:alpha=1"
        if fout is not None:
            f += f",fade=t=out:st={fout:.3f}:d={d}:alpha=1"
        return f + f"[{out_lab}]"
    graph = ";".join([
        f"color=c=black:s={SW}x{SH}:r={FPS}:d={seconds + 1:.2f}[bg]",
        ns._moving("0:v", SW, SH, "nm", 0, zoom=0.06),
        ns._moving("1:v", SW, SH, "pm", 3, zoom=0.06),
        "[pm]format=rgba[pr]",
        f"[4:v]format=gray,crop={SW}:{SH}:x=0:y='{SH}-({m_e})'[mk]",
        "[pr][mk]alphamerge[pa]",
        f"[bg][nm]overlay=eval=frame:x=0:y='{y_now}'[a0]",
        f"[a0][pa]overlay=eval=frame:x=0:y='{y_then}'[a1]",
        f"[a1]drawbox=x=0:y='{b}-4':w={SW}:h=8:color=0xFF9628@0.95:t=fill:"
        f"enable='between(t,{split_at:.3f},{take_at + SLIDE:.3f})'[a2]",
        fade("2:v", "nt", fout=take_at - 0.2),
        "[a2][nt]overlay=48:150[a3]",
        fade("3:v", "tt", fin=roll_at - 0.3),
        f"[a3][tt]overlay=eval=frame:x=48:y='{b}+40'[a4]",
        f"[5:v]tpad=stop_mode=clone:stop_duration={seconds:.2f},"
        f"setpts=PTS-STARTPTS+{roll_at:.3f}/TB[c0]",
        fade("c0", "cn", fin=roll_at - 0.1, fout=take_at - 0.35, d=0.3),
        f"[a4][cn]overlay=x={(SW - cw[0]) // 2}:y={960 - cw[1] // 2}:"
        f"enable='between(t,{roll_at - 0.1:.3f},{take_at + 0.1:.3f})'[a5]",
        f"[a5]trim=end_frame={frames},format=yuv420p[v]",
    ])
    loop = ["-loop", "1", "-framerate", str(FPS), "-i"]
    run(["ffmpeg", "-y", *loop, now_png, *loop, then_png, *loop, nowtag, *loop, thentag,
         *loop, mask, "-framerate", str(FPS), "-i", pattern,
         "-filter_complex", graph, "-map", "[v]", "-frames:v", str(frames), "-r", str(FPS),
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", out])
    return ticks


def end_clip(now_png, then_png, seconds, end, then_year, work, out):
    """The file closes: today down from the top, the past up from the
    bottom, an orange seam, THE FULL STORY on it, a bouncing pointer."""
    import datetime as dt
    now_year = dt.date.today().year
    half = SH // 2
    top = Image.open(now_png).crop((0, half // 2, SW, half // 2 + half))
    bot = Image.open(then_png).crop((0, half // 2, SW, half // 2 + half))
    top = ImageEnhance.Brightness(top).enhance(0.55)
    bot = ImageEnhance.Brightness(bot).enhance(0.55)
    tp, bp = os.path.join(work, "end_top.png"), os.path.join(work, "end_bot.png")
    top.save(tp)
    bot.save(bp)
    # the words: a dark band across the seam
    card = Image.new("RGBA", (SW, SH), (0, 0, 0, 0))
    d = ImageDraw.Draw(card)
    d.rectangle((0, half - 190, SW, half + 190), fill=(6, 8, 14, 225))
    d.rectangle((0, half - 4, SW, half + 4), fill=ORANGE + (255,))
    f1 = font(132)
    d.text((SW // 2, half - 80), end["title"], font=f1, anchor="mm", fill=(255, 255, 255, 255))
    f2 = font(50)
    sub = end["sub"]
    while f2.getlength(sub) > SW - 80 and f2.size > 30:
        f2 = font(f2.size - 2)
    d.text((SW // 2, half + 95), sub, font=f2, anchor="mm", fill=ORANGE + (255,))
    for text, y in ((f"{now_year}", 150), (f"{then_year}", SH - 380)):
        ns._tag(text).save(os.path.join(work, "t.png"))
        t = Image.open(os.path.join(work, "t.png"))
        card.alpha_composite(t, (48, y))
    cp = os.path.join(work, "end_card.png")
    card.save(cp)
    ptr = Image.new("RGBA", (SW, 220), (0, 0, 0, 0))
    d = ImageDraw.Draw(ptr)
    f3 = font(64)
    d.text((SW // 2, 60), end["pointer"], font=f3, anchor="mm", fill=(255, 255, 255, 255),
           stroke_width=7, stroke_fill=(0, 0, 0, 255))
    # an arrow down and to the left, where Shorts show the related video
    d.polygon([(250, 130), (330, 130), (290, 205)], fill=ORANGE + (255,))
    pp = os.path.join(work, "end_ptr.png")
    ptr.save(pp)

    def ease(start, length):
        p = f"clip((t-{start:.3f})/{length:.3f},0,1)"
        return f"({p}*{p}*(3-2*{p}))"
    frames = max(1, round(seconds * FPS))
    graph = ";".join([
        f"color=c=black:s={SW}x{SH}:r={FPS}:d={seconds + 1:.2f}[bg]",
        f"[bg][0:v]overlay=eval=frame:x=0:y='-{half}+{half}*{ease(0.0, 0.7)}'[a0]",
        f"[a0][1:v]overlay=eval=frame:x=0:y='{SH}-{half}*{ease(0.0, 0.7)}'[a1]",
        "[2:v]format=rgba,fade=t=in:st=0.6:d=0.4:alpha=1[cd]",
        "[a1][cd]overlay=0:0[a2]",
        "[3:v]format=rgba,fade=t=in:st=1.1:d=0.4:alpha=1[pt]",
        f"[a2][pt]overlay=eval=frame:x=0:y='{SH - 520}+18*sin(2*PI*1.4*t)'[a3]",
        f"[a3]trim=end_frame={frames},format=yuv420p[v]",
    ])
    loop = ["-loop", "1", "-framerate", str(FPS), "-i"]
    run(["ffmpeg", "-y", *loop, tp, *loop, bp, *loop, cp, *loop, pp,
         "-filter_complex", graph, "-map", "[v]", "-frames:v", str(frames), "-r", str(FPS),
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", out])


# ---------------------------------------------------------------- the Short

def main(folder, out):
    work = os.path.join(out, "work")
    os.makedirs(work, exist_ok=True)
    os.environ["OWNER_MEDIA_DIR"] = os.path.abspath(folder)
    with open(os.path.join(folder, "short.json"), encoding="utf-8") as f:
        spec = json.load(f)
    then_year = spec["then_year"]

    # pictures
    now_src = Image.open(owner_media.opening("now")[0]).convert("RGB")
    then_src = Image.open(owner_media.opening("then")[0]).convert("RGB")
    now_png, then_png = os.path.join(work, "now.png"), os.path.join(work, "then.png")
    cover(now_src, NOW_FOCUS, at=(0.5, 0.40)).save(now_png)
    grade_then(band(then_src, THEN_FOCUS, zoom=1.3)).save(then_png)
    raw = {}
    for key, img in spec["images"].items():
        raw[key] = fetch(img["url"])
        print(f"🖼️  {key}: {raw[key].size}")

    # the voice
    from audio_engine import generate_audio_pipeline
    lines = spec["lines"]
    scenes = generate_audio_pipeline({"scenes": [{"narration": l["say"]} for l in lines]},
                                     work)["scenes"]
    starts, t = [], LEAD
    for i, s in enumerate(scenes):
        starts.append(t)
        t += s["duration"] + GAP
    # the opening needs room: split on "But", the years roll and hold
    split_at = starts[1] - 0.15
    need = split_at + SPLIT_IN + ROLL + 0.9 + SLIDE + 0.2
    first_after = 2
    if starts[first_after] < need:
        push = need - starts[first_after]
        starts = starts[:first_after] + [x + push for x in starts[first_after:]]
    take_at = starts[first_after] - SLIDE - 0.2
    ends = [starts[i] + scenes[i]["duration"] for i in range(len(scenes))]
    total = ends[-1] + CARD_HOLD

    # visual segments: [opening][one per line ...][end card]
    bounds = [0.0]
    shows = ["opening"]
    for i in range(first_after, len(lines)):
        bounds.append(starts[i] - 0.15)
        shows.append(lines[i]["show"])
    bounds.append(total)
    clips = []
    ticks = None
    for k, show in enumerate(shows):
        seg = bounds[k + 1] - bounds[k] + (XF if k + 1 < len(shows) else 0)
        path = os.path.join(work, f"clip_{k:02d}.mp4")
        line = lines[first_after + k - 1] if k else None
        if show == "opening":
            ticks = opening_clip(now_png, then_png, split_at, take_at, seg, then_year, work, path)
        elif show == "end":
            end_clip(now_png, then_png, seg, spec["end"], then_year, work, path)
        elif show == "now":
            # back to today: the paradox closes where it opened
            lp = os.path.join(work, f"layer_{k:02d}.png")
            cover(now_src, NOW_FOCUS, zoom=1.15, at=(0.5, 0.38)).save(lp)
            tag = os.path.join(work, f"tag_{k:02d}.png")
            ns._tag(line.get("tag") or "NOW").save(tag)
            still_clip(lp, seg, path, tag)
        else:
            img = spec["images"][show]
            layer = layer_for(raw[show], img.get("focus", (0.5, 0.4)), line.get("zoom"))
            lp = os.path.join(work, f"layer_{k:02d}.png")
            layer.save(lp)
            tag = None
            if line.get("tag"):
                tag = os.path.join(work, f"tag_{k:02d}.png")
                ns._tag(line["tag"]).save(tag)
            still_clip(lp, seg, path, tag)
        clips.append(path)
        print(f"🎞️  {show}: {bounds[k]:.1f}-{bounds[k + 1]:.1f}s")

    # dissolves at the bounds
    ins, chain, last = [], [], "0:v"
    for p in clips:
        ins += ["-i", p]
    for k in range(1, len(clips)):
        lab = f"x{k}"
        chain.append(f"[{last}][{k}:v]xfade=transition=fade:duration={XF}:"
                     f"offset={bounds[k]:.3f}[{lab}]")
        last = lab
    joined = os.path.join(work, "joined.mp4")
    run(["ffmpeg", "-y", *ins, "-filter_complex", ";".join(chain), "-map", f"[{last}]",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", joined])

    # the words, under the picture (none on the card: it has its own)
    caps, k = [], 0
    for i, s in enumerate(scenes[:-1]):
        for text, a, b in chunks(s.get("words") or [], starts[i]):
            p = os.path.join(work, f"cap_{k:03d}.png")
            w = caption_png(text.upper(), p)
            caps.append((p, w, a, min(b, starts[i] + s["duration"] + GAP)))
            k += 1
    ins, chain, last = ["-i", joined], [], "0:v"
    for n, (p, w, a, b) in enumerate(caps, start=1):
        ins += ["-i", p]
        chain.append(f"[{last}][{n}:v]overlay=x={(SW - w) // 2}:y=1300:"
                     f"enable='between(t,{a:.3f},{b:.3f})'[c{n}]")
        last = f"c{n}"
    captioned = os.path.join(work, "captioned.mp4")
    run(["ffmpeg", "-y", *ins, "-filter_complex", ";".join(chain), "-map", f"[{last}]",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
         captioned])

    # the sound: the lines where they start, the counter's ticks, the music under
    ins, parts = [], []
    for i, s in enumerate(scenes):
        ins += ["-i", s["audio_path"]]
        ms = int(starts[i] * 1000)
        parts.append(f"[{i}:a]adelay={ms}|{ms},apad[v{i}]")
    n = len(scenes)
    mix = "".join(f"[v{i}]" for i in range(n))
    extra = []
    if ticks:
        ins += ["-i", ticks]
        extra.append(f"[{n}:a]apad[tk]")
        mix += "[tk]"
        n += 1
    music = next((os.path.join("music", f) for f in sorted(os.listdir("music"))
                  if f.startswith(spec.get("music", "")) and f.endswith(".mp3")), None)
    if music:
        ins += ["-stream_loop", "-1", "-i", music]
        extra.append(f"[{n}:a]volume=0.13,afade=t=out:st={total - 1.5:.2f}:d=1.5[mu]")
        mix += "[mu]"
        n += 1
    count = mix.count("[")
    graph = ";".join(parts + extra + [f"{mix}amix=inputs={count}:normalize=0:duration=longest,"
                                       f"atrim=0:{total:.3f}[a]"])
    final = os.path.join(out, "short.mp4")
    run(["ffmpeg", "-y", "-i", captioned, *ins, "-filter_complex",
         _shift(graph, 1),
         "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
         "-shortest", "-movflags", "+faststart", final])
    credits = [img["credit"] for img in spec["images"].values()]
    with open(os.path.join(out, "short.json"), "w", encoding="utf-8") as f:
        json.dump({"seconds": round(total, 2), "lines": [l["say"] for l in lines],
                   "credits": credits, "music": music}, f, ensure_ascii=False, indent=1)
    print(f"✅ Short: {final} ({total:.1f}s)")


def _shift(graph, by):
    """Input numbers in the sound graph move up by one: the video is input 0."""
    return re.sub(r"\[(\d+):a\]", lambda m: f"[{int(m.group(1)) + by}:a]", graph)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "out")
