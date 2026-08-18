"""
Audio engine — Microsoft Edge neural TTS (free, unlimited, near-human quality).

The killer feature vs gTTS: edge-tts streams WordBoundary events, giving us the
exact millisecond each word is spoken. That powers the word-synced captions in
video_engine.py, which is what makes Shorts captions look professional.
"""

import asyncio
import os
import re
import subprocess

import edge_tts

# Letters or digits in any script the narration might use. Punctuation, em
# dashes and stray markup are not speech: both TTS engines reject text made
# only of those, gTTS by assertion rather than by exception.
_SPEAKABLE = re.compile(r"[^\W_]", re.UNICODE)


def is_speakable(text: str) -> bool:
    """Whether a TTS engine can make sound out of this."""
    return bool(_SPEAKABLE.search(text or ""))

# Deep, calm British male — fits the dark-documentary tone.
# Other strong options: en-US-AndrewMultilingualNeural (warm US male),
# en-GB-SoniaNeural (female), en-US-BrianMultilingualNeural.
# Preview any voice with:  edge-tts --list-voices
VOICE = os.getenv("TTS_VOICE", "en-GB-RyanNeural")
RATE = os.getenv("TTS_RATE", "+8%")    # slightly faster = better Shorts retention
PITCH = os.getenv("TTS_PITCH", "-4Hz")  # slightly deeper = more gravitas


async def _synthesize(text: str, output_path: str, rate: str = None):
    """Generates the MP3 and returns word timings: [(word, start_s, end_s), ...].

    rate overrides the global narration speed for this scene. The long-form arc
    reads its midpoint slower than its opening on purpose — see PACING in
    longform_llm — and a single global rate made that impossible.
    """
    communicate = edge_tts.Communicate(text, VOICE, rate=rate or RATE,
                                       pitch=PITCH, boundary="WordBoundary")
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




# ---------------------------------------------------------------------------
# Dead air
#
# edge-tts pads every clip it returns: a beat of near-silence before the first
# word and another after the last. On a fifty-second Short that is invisible.
# On a seven-minute video assembled from forty separate clips it is most of a
# minute of nothing, distributed as a small hesitation at every single cut —
# which is exactly where a viewer is already deciding whether to stay.
#
# The trim has to be measured rather than guessed, because the word timings
# edge-tts hands back are offsets into the UNtrimmed audio. Cutting the head
# without shifting them by the same amount would desynchronise every caption in
# the video. So: measure, cut, shift by the measured amount.
# ---------------------------------------------------------------------------

# Anything under this is silence for our purposes. Narration recorded at -4Hz
# pitch still sits far above it, so this does not clip speech.
SILENCE_DB = -45

# Silence has to last this long to count as padding rather than a plosive.
SILENCE_MIN = 0.05

# Left behind at each end so a trimmed clip does not start on the attack of the
# first consonant.
SILENCE_KEEP = 0.06

# MP3 encoder padding puts the last decoded sample slightly before the
# container's reported duration, so "ends at the end" needs a tolerance.
EOF_TOLERANCE = 0.15

_SILENCE_START = re.compile(r"silence_start:\s*(-?[\d.]+)")
_SILENCE_END = re.compile(r"silence_end:\s*([\d.]+)")


def _measure_silence(path: str, duration: float):
    """(leading, trailing) seconds of silence in this clip.

    Uses ffmpeg's silencedetect, which reports every silent run; only a run
    starting at the very beginning and one ending at the very end are padding.
    """
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", path,
         "-af", f"silencedetect=noise={SILENCE_DB}dB:d={SILENCE_MIN}",
         "-f", "null", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False)
    log = result.stdout or ""

    starts = [float(m) for m in _SILENCE_START.findall(log)]
    ends = [float(m) for m in _SILENCE_END.findall(log)]

    lead = 0.0
    if starts and starts[0] <= 0.02 and ends:
        lead = ends[0]

    trailing = 0.0
    if len(starts) > len(ends):
        # A silence that opened and never closed runs to the end of the file.
        trailing = duration - starts[-1]
    elif starts and ends and ends[-1] >= duration - EOF_TOLERANCE:
        # silencedetect closes the final run at the last decoded sample, which
        # for an MP3 sits a little short of the container duration because of
        # encoder padding. Anything landing that close to the end IS the end.
        trailing = duration - starts[-1]
    trailing = max(0.0, min(trailing, duration))

    # A clip that is silent end to end is a failed synthesis, not a padded one.
    if lead + trailing >= duration - 0.05:
        return 0.0, 0.0
    return lead, trailing


def tighten_clip(path: str, duration: float, pause_after: float = 0.0):
    """Trims the padding off both ends and appends a deliberate beat.

    Returns (seconds_removed_from_the_head, new_duration). The caller must
    shift that scene's word timings back by the first value or every caption
    after the cut runs early.
    """
    try:
        lead, trailing = _measure_silence(path, duration)
    except Exception as e:
        print(f"   ⚠️ Could not measure silence ({type(e).__name__}: {e}) — "
              f"leaving the clip as recorded.")
        return 0.0, duration

    start = max(0.0, lead - SILENCE_KEEP)
    end = duration - max(0.0, trailing - SILENCE_KEEP)

    # Nothing worth doing, and re-encoding for nothing costs quality.
    if start < 0.02 and end > duration - 0.02 and pause_after <= 0:
        return 0.0, duration
    if end - start < 0.2:
        return 0.0, duration

    chain = [f"atrim=start={start:.3f}:end={end:.3f}", "asetpts=N/SR/TB"]
    if pause_after > 0:
        chain.append(f"apad=pad_dur={pause_after:.3f}")

    tmp = path + ".tight.mp3"
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", path,
             "-af", ",".join(chain), "-q:a", "2", tmp],
            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, check=True)
        os.replace(tmp, path)
    except Exception as e:
        print(f"   ⚠️ Could not tighten the clip ({type(e).__name__}: {e}) — "
              f"leaving it as recorded.")
        if os.path.exists(tmp):
            os.remove(tmp)
        return 0.0, duration

    return start, get_audio_duration(path)


def shift_word_timings(words, removed: float):
    """Moves word timings back by the silence cut off the head of the clip."""
    if not words or removed <= 0:
        return words
    shifted = []
    for w in words:
        start = w["start"] - removed
        end = w["end"] - removed
        # A word that started inside the trimmed silence keeps its text but
        # begins at zero; dropping it would lose a caption chunk.
        shifted.append({"word": w["word"], "start": max(0.0, start),
                        "end": max(0.0, end)})
    return shifted


def generate_audio_pipeline(script_data: dict, output_dir: str = "build") -> dict:
    print(f"🎙️ Initializing Audio Engine (voice: {VOICE})...")
    os.makedirs(output_dir, exist_ok=True)

    skipped, trimmed_total = 0, 0.0
    for idx, scene in enumerate(script_data.get("scenes", [])):
        audio_path = os.path.join(output_dir, f"scene_{idx}.mp3")
        text = scene.get("narration", "").strip()

        # A scene with nothing sayable in it is not a synthesis problem, it is
        # a script problem, and it must not reach the TTS engines. gTTS asserts
        # "No text to send to TTS API" on punctuation-only input, which once
        # killed a run twenty-seven minutes in, after the whole script had
        # already been written. Skipping the scene costs a few seconds of
        # video; raising costs the entire run.
        if not is_speakable(text):
            print(f"⚠️ Scene {idx}: no speakable narration ({text[:30]!r}) — "
                  f"skipping this scene.")
            skipped += 1
            continue

        rate = scene.get("tts_rate") or RATE
        print(f"🗣️ Synthesizing scene {idx} at {rate}: '{text[:40]}...'")

        try:
            words = asyncio.run(_synthesize(text, audio_path, rate=rate))
            if os.path.getsize(audio_path) < 1000:  # suspiciously small = failed
                raise RuntimeError("edge-tts produced an empty file")
        except Exception as e:
            print(f"⚠️ edge-tts failed ({e}) — falling back to gTTS for this scene.")
            try:
                words = _synthesize_fallback(text, audio_path)
            except Exception as e2:
                # Both engines gone for this scene. Every other scene still has
                # a video to be part of.
                print(f"❌ Scene {idx}: gTTS also failed ({type(e2).__name__}: "
                      f"{e2}) — skipping this scene.")
                skipped += 1
                continue

        duration = get_audio_duration(audio_path)

        # Cut the padding, add the beat this scene is supposed to end on, and
        # move the word timings to match what is now in the file.
        removed, duration = tighten_clip(audio_path, duration,
                                         float(scene.get("pause_after") or 0.0))
        words = shift_word_timings(words, removed)
        trimmed_total += removed

        scene["audio_path"] = audio_path
        scene["duration"] = duration
        scene["words"] = words
        cut = f", -{removed:.2f}s dead air" if removed > 0.01 else ""
        print(f"⏱️ Scene {idx}: {duration:.2f}s, {len(words)} word timings{cut}")

    note = f" ({skipped} scene(s) skipped)" if skipped else ""
    if trimmed_total > 0.5:
        print(f"✂️  Removed {trimmed_total:.1f}s of dead air across the video.")
    print(f"✅ Audio pipeline complete{note}.")
    return script_data


if __name__ == "__main__":
    test = {"scenes": [{"narration": "In the winter of 1914, an entire army vanished without firing a single shot."}]}
    out = generate_audio_pipeline(test)
    print(out["scenes"][0]["words"][:5])
