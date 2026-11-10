"""
Checks a rendered Short before it is posted (exit 1 = do not post):
size 1080x1920, 35-65 s, sound, and no black / frozen frames.

    python tools/check_short.py short.mp4 [frames.jpg]
"""
import json
import subprocess
import sys


def probe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height",
                        "-show_entries", "format=duration", "-of", "json", path],
                       capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


def problems(info, black_s):
    out = []
    vs = [s for s in info["streams"] if s["codec_type"] == "video"]
    if not vs or (vs[0]["width"], vs[0]["height"]) != (1080, 1920):
        out.append("not 1080x1920")
    if not any(s["codec_type"] == "audio" for s in info["streams"]):
        out.append("no sound")
    d = float(info["format"]["duration"])
    if not 35 <= d <= 65:
        out.append(f"{d:.0f} s long (35-65 wanted)")
    if black_s > 1.0:
        out.append(f"{black_s:.1f} s of black frames")
    return out


def black_seconds(path):
    r = subprocess.run(["ffmpeg", "-i", path, "-vf", "blackdetect=d=0.2:pix_th=0.05", "-an", "-f", "null", "-"],
                       capture_output=True, text=True)
    import re
    return sum(float(m) for m in re.findall(r"black_duration:([\d.]+)", r.stderr))


def speech_language(path):
    """The language the narration is spoken in (faster-whisper, tiny model),
    or None when it cannot be checked (owner, 10 Nov: a Short must be English)."""
    try:
        import tempfile
        from faster_whisper import WhisperModel
        wav = tempfile.mktemp(suffix=".wav")
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", path, "-t", "25", "-ac", "1",
                        "-ar", "16000", wav], check=True)
        _, info = WhisperModel("tiny", device="cpu", compute_type="int8").transcribe(wav)
        return info.language
    except Exception as e:
        print(f"⚠️ Language check unavailable: {type(e).__name__}")
        return None


if __name__ == "__main__":
    path = sys.argv[1]
    p = problems(probe(path), black_seconds(path))
    lang = speech_language(path)
    print(f"🗣️ narration language: {lang}")
    if lang and lang != "en":
        p.append(f"narration is {lang}, not English")
    if len(sys.argv) > 2:
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", path, "-vf",
                        "fps=1,scale=180:-1,tile=10x6", "-frames:v", "1", sys.argv[2]])
    print("❌ " + "; ".join(p) if p else "✅ checks passed")
    sys.exit(1 if p else 0)
