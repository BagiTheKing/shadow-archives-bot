"""
Audio engine — Microsoft Edge neural TTS (free, unlimited, near-human quality).

The killer feature vs gTTS: edge-tts streams WordBoundary events, giving us the
exact millisecond each word is spoken. That powers the word-synced captions in
video_engine.py, which is what makes Shorts captions look professional.
"""

import asyncio
import os
import subprocess

import edge_tts

# Deep, calm British male — fits the dark-documentary tone.
# Other strong options: en-US-AndrewMultilingualNeural (warm US male),
# en-GB-SoniaNeural (female), en-US-BrianMultilingualNeural.
# Preview any voice with:  edge-tts --list-voices
VOICE = os.getenv("TTS_VOICE", "en-GB-RyanNeural")
RATE = os.getenv("TTS_RATE", "+8%")    # slightly faster = better Shorts retention
PITCH = os.getenv("TTS_PITCH", "-4Hz")  # slightly deeper = more gravitas


async def _synthesize(text: str, output_path: str):
    """Generates the MP3 and returns word timings: [(word, start_s, end_s), ...]."""
    communicate = edge_tts.Communicate(text, VOICE, rate=RATE, pitch=PITCH)
    words = []
    with open(output_path, "wb") as f:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                start = chunk["offset"] / 10_000_000  # 100-ns units -> seconds
                end = start + chunk["duration"] / 10_000_000
                words.append({"word": chunk["text"], "start": start, "end": end})
    return words


def _synthesize_fallback(text: str, output_path: str):
    """Emergency fallback if Microsoft's endpoint is unreachable. Robotic, but the
    pipeline survives. No word timings — captions degrade to per-scene mode."""
    from gtts import gTTS

    gTTS(text=text, lang="en", tld="co.uk").save(output_path)
    return []


def get_audio_duration(file_path: str) -> float:
    cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", file_path,
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, check=True)
    return float(result.stdout.strip())


def generate_audio_pipeline(script_data: dict, output_dir: str = "build") -> dict:
    print(f"🎙️ Initializing Audio Engine (voice: {VOICE})...")
    os.makedirs(output_dir, exist_ok=True)

    for idx, scene in enumerate(script_data.get("scenes", [])):
        audio_path = os.path.join(output_dir, f"scene_{idx}.mp3")
        text = scene.get("narration", "").strip()
        print(f"🗣️ Synthesizing scene {idx}: '{text[:40]}...'")

        try:
            words = asyncio.run(_synthesize(text, audio_path))
            if os.path.getsize(audio_path) < 1000:  # suspiciously small = failed
                raise RuntimeError("edge-tts produced an empty file")
        except Exception as e:
            print(f"⚠️ edge-tts failed ({e}) — falling back to gTTS for this scene.")
            words = _synthesize_fallback(text, audio_path)

        scene["audio_path"] = audio_path
        scene["duration"] = get_audio_duration(audio_path)
        scene["words"] = words
        print(f"⏱️ Scene {idx}: {scene['duration']:.2f}s, {len(words)} word timings")

    print("✅ Audio pipeline complete.")
    return script_data


if __name__ == "__main__":
    test = {"scenes": [{"narration": "In the winter of 1914, an entire army vanished without firing a single shot."}]}
    out = generate_audio_pipeline(test)
    print(out["scenes"][0]["words"][:5])
