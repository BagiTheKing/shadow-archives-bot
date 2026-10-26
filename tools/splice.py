"""
Changes a finished render without rendering it again (owner, 26 Oct: "we
already had the video, we just had to glue the new opening"): the owner's
approved opening clip is laid over the first seconds, covering the burned-in
subtitles too, and dissolves into the video over its last 0.7 s. The
sound is the render's own. Run by .github/workflows/splice_video.yml.

    python tools/splice.py render.mp4 owner_media/<story> out.mp4
"""

import os
import subprocess
import sys

DISSOLVE = 0.7


def duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", path], capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def splice_opening(render, clip, out):
    d = duration(clip)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", render, "-i", clip,
                    "-filter_complex",
                    f"[1:v]format=yuva420p,fade=t=out:st={d - DISSOLVE:.2f}:d={DISSOLVE}:alpha=1,"
                    "setpts=PTS-STARTPTS[o];[0:v][o]overlay=0:0:eof_action=pass,format=yuv420p[v]",
                    "-map", "[v]", "-map", "0:a", "-c:v", "libx264", "-preset", "veryfast",
                    "-crf", "18", "-c:a", "copy", "-movflags", "+faststart", out], check=True)
    print(f"✅ Opening ({d:.1f}s) laid over {render} → {out}")


if __name__ == "__main__":
    render, folder, out = sys.argv[1:4]
    clip = os.path.join(folder, "opening_clip.mp4")
    if not os.path.exists(clip):
        raise SystemExit(f"❌ No opening_clip.mp4 in {folder}")
    splice_opening(render, clip, out)
