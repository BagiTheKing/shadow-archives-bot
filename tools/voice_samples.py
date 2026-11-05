"""
The same lines in several voices, for the owner to choose by ear (owner,
5 Nov: "send the samples"). Run by .github/workflows/voice_samples.yml.

    python tools/voice_samples.py out_dir

Writes out_dir/<n>_<voice>.mp3 and out_dir/all_voices.mp3 (each one
announced by its number, a short pause between them).
"""

import asyncio
import os
import subprocess
import sys

TEXT = ("This week, Paraguay's president resigned in a scandal over a failed bank. "
        "But in 2001, next door in Argentina, a bank freeze toppled a president in under three weeks. "
        "Riots filled the Plaza de Mayo. Thirty-nine people were killed across the country.")

KOKORO = ["am_onyx", "am_michael", "bm_george", "bm_lewis", "am_adam"]
EDGE = ["en-US-AndrewMultilingualNeural", "en-US-BrianMultilingualNeural",
        "en-US-ChristopherNeural", "en-GB-RyanNeural", "en-GB-ThomasNeural", "en-US-GuyNeural"]


def kokoro(voice, path):
    import soundfile as sf
    from kokoro_onnx import Kokoro
    k = Kokoro("models/kokoro-v1.0.onnx", "models/voices-v1.0.bin")
    samples, sr = k.create(TEXT, voice=voice, speed=1.05, lang="en-us" if voice[0] == "a" else "en-gb")
    sf.write(path + ".wav", samples, sr)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", path + ".wav", "-b:a", "128k", path], check=True)
    os.remove(path + ".wav")


async def edge(voice, path):
    import edge_tts
    await edge_tts.Communicate(TEXT, voice, rate="+5%").save(path)


def main(out):
    os.makedirs(out, exist_ok=True)
    made, n = [], 0
    for v in KOKORO + EDGE:
        n += 1
        path = os.path.join(out, f"{n:02d}_{v}.mp3")
        try:
            if v in KOKORO:
                kokoro(v, path)
            else:
                asyncio.run(edge(v, path))
            made.append((n, v, path))
            print(f"✅ {n:02d} {v}")
        except Exception as e:
            print(f"⚠️ {v}: {type(e).__name__}: {e}")
    # one file: "Voice one" (in a plain voice), the sample, a pause
    parts = []
    for n, v, path in made:
        tag = os.path.join(out, f"tag_{n:02d}.mp3")
        asyncio.run(edge_tts_tag(f"Voice {n}.", tag))
        parts += [tag, path]
    lst = os.path.join(out, "list.txt")
    with open(lst, "w") as f:
        for p in parts:
            f.write(f"file '{os.path.abspath(p)}'\n")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst,
                    "-af", "apad=pad_dur=0.6", "-ar", "24000", "-ac", "1", "-b:a", "128k",
                    os.path.join(out, "all_voices.mp3")], check=True)
    for n, _, _ in made:
        os.remove(os.path.join(out, f"tag_{n:02d}.mp3"))
    os.remove(lst)


async def edge_tts_tag(text, path):
    import edge_tts
    await edge_tts.Communicate(text, "en-US-JennyNeural").save(path)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "out")
