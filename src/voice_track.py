"""
A Short's voice from a recording instead of Kokoro (owner, 5 Nov: the Claude
app reads its messages aloud; "record yours and use it for the video").

The owner screen-records the app reading a message that holds only the
Short's lines, and the file goes into the story's owner folder. This finds
each line in it: the recording is transcribed with word times
(faster-whisper), the transcript is matched to the script word by word, and
each line is cut out with its own word times, so the captions stay on the
words. Captions show the script's spelling, not the transcript's.

    lines_from_recording("owner_media/x/voice.m4a", ["This week, ...", ...], work)

returns what audio_engine.generate_audio_pipeline returns per scene:
{"audio_path", "duration", "words": [{"word", "start", "end"}]}.
"""

import difflib
import os
import re
import subprocess

MODEL = os.getenv("VOICE_MODEL", "small.en")
PAD_BEFORE, PAD_AFTER = 0.06, 0.18


def _norm(w):
    return re.sub(r"[^a-z0-9]", "", w.lower())


def transcribe(path):
    from faster_whisper import WhisperModel
    model = WhisperModel(MODEL, device="cpu", compute_type="int8")
    segments, _ = model.transcribe(path, word_timestamps=True, vad_filter=False,
                                   language="en", beam_size=5)
    words = []
    for seg in segments:
        for w in seg.words or []:
            if _norm(w.word):
                words.append({"word": w.word.strip(), "start": float(w.start), "end": float(w.end)})
    return words


def _spoken(token):
    """Script tokens as they are said: '250' is several words in speech."""
    return [_norm(token)] if _norm(token) else []


def lines_from_recording(path, lines, work):
    os.makedirs(work, exist_ok=True)
    wav = os.path.join(work, "voice_full.wav")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", path, "-vn", "-ac", "1",
                    "-ar", "24000", wav], check=True)
    heard = transcribe(wav)
    if not heard:
        raise SystemExit("❌ Nothing was heard in the recording")
    print(f"🎙️  Recording: {len(heard)} words heard, {heard[0]['start']:.1f}-{heard[-1]['end']:.1f}s")

    # the script, word by word, with the line each word belongs to
    script = []
    for li, line in enumerate(lines):
        for tok in line.split():
            script.append((li, tok))
    a = [_norm(t) for _, t in script]
    b = [_norm(w["word"]) for w in heard]
    times = [None] * len(script)
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                times[i1 + k] = (heard[j1 + k]["start"], heard[j1 + k]["end"])
        elif tag == "replace" and j2 > j1:
            # numbers and names heard differently ("250" / "two hundred and
            # fifty"): the script words share the heard span evenly
            t0, t1 = heard[j1]["start"], heard[j2 - 1]["end"]
            n = i2 - i1
            for k in range(n):
                times[i1 + k] = (t0 + (t1 - t0) * k / n, t0 + (t1 - t0) * (k + 1) / n)
    matched = sum(1 for t in times if t)
    if matched < 0.6 * len(script):
        raise SystemExit(f"❌ The recording does not match the lines ({matched}/{len(script)} words)")
    # words still without a time sit between their neighbours
    for i in range(len(times)):
        if times[i] is None:
            prev = next((times[j][1] for j in range(i - 1, -1, -1) if times[j]), heard[0]["start"])
            nxt = next((times[j][0] for j in range(i + 1, len(times)) if times[j]), heard[-1]["end"])
            times[i] = (prev, max(prev + 0.05, nxt))

    scenes = []
    for li, line in enumerate(lines):
        idx = [i for i, (l, _) in enumerate(script) if l == li]
        if not idx:
            continue
        t0 = max(0.0, times[idx[0]][0] - PAD_BEFORE)
        # never into the next line's first word
        nxt = times[idx[-1] + 1][0] if idx[-1] + 1 < len(times) else None
        t1 = times[idx[-1]][1] + PAD_AFTER
        if nxt is not None:
            t1 = min(t1, max(times[idx[-1]][1] + 0.02, nxt - 0.02))
        out = os.path.join(work, f"voice_{li:02d}.wav")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t0:.3f}", "-to", f"{t1:.3f}",
                        "-i", wav, "-af", "afade=t=in:d=0.02", out], check=True)
        words = [{"word": script[i][1], "start": round(times[i][0] - t0, 3),
                  "end": round(times[i][1] - t0, 3)} for i in idx]
        scenes.append({"audio_path": out, "duration": round(t1 - t0, 3), "words": words})
        print(f"   line {li + 1}: {t1 - t0:.1f}s  {line[:50]}")
    return scenes
