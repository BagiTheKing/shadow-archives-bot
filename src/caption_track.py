"""
Caption track — the SRT that is uploaded alongside the video.

The captions video_engine burns into the picture are pixels. YouTube cannot read
them, so every video published so far carries no transcript: nothing for search
to index, nothing for the "CC" button, nothing for anyone watching muted who
needs the words rather than the style. Search is 1.4% of this channel's traffic
and the videos it does reach are a poor match; an indexable transcript is the
cheapest thing that can move that.

This is deliberately NOT the same text as the on-screen captions. Those are
two-to-three word chunks in caps, tuned to be read at a glance over the picture.
A transcript wants sentences in ordinary case, which is what a human reads and
what an indexer parses.

Real timings come free from edge-tts and Azure (audio_engine records a
WordBoundary event for every word). Scenes narrated by Kokoro or the gTTS
emergency fallback get estimated timings instead — audio_engine's
_estimate_word_timings weights each word by letter count and adds a pause
after punctuation, which is not measured alignment but tracks speech rhythm
far better than spreading the sentence evenly by word count. Either way
every scene reaches this module with a `words` list; _group_untimed below
is now only a last-resort for the rare scene that has none at all.
"""

import os
import re

SRT_NAME = "captions.srt"

# A cue is a line someone reads while listening. Past roughly a dozen words it
# stops being readable at a glance, and past six seconds it has outstayed the
# sentence it belongs to.
MAX_CUE_WORDS = 12
MAX_CUE_SECONDS = 6.0

# Sentence-final punctuation. Em dashes deliberately excluded: this narration
# uses them mid-sentence, and breaking there splits a clause in half.
_SENTENCE_END = (".", "!", "?")


def _srt_time(seconds: float) -> str:
    """SRT wants HH:MM:SS,mmm — comma before the milliseconds, not a period."""
    seconds = max(0.0, seconds)
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _ends_sentence(word: str) -> bool:
    # Strip closing quotes and brackets so 'over."' still reads as a full stop.
    return word.rstrip().rstrip('"\'”’)]').endswith(_SENTENCE_END)


def _group_timed_words(words):
    """Groups word timings into cues, breaking on sentence ends first and on the
    length ceilings otherwise. Returns [(start, end, text), ...] in scene-local
    seconds."""
    cues, current = [], []
    for w in words:
        current.append(w)
        span = w["end"] - current[0]["start"]
        if (_ends_sentence(w["word"])
                or len(current) >= MAX_CUE_WORDS
                or span >= MAX_CUE_SECONDS):
            cues.append(current)
            current = []
    if current:
        cues.append(current)

    out = []
    for group in cues:
        text = " ".join(w["word"] for w in group).strip()
        if text:
            out.append((group[0]["start"], group[-1]["end"], text))
    return out


def _group_untimed(narration: str, duration: float):
    """No WordBoundary events for this scene — spread the words evenly instead.

    Rough, but a transcript that is a second out of step is still a transcript;
    omitting the scene would leave a hole in the middle of the video."""
    words = (narration or "").split()
    if not words or duration <= 0:
        return []

    groups, current = [], []
    for word in words:
        current.append(word)
        if _ends_sentence(word) or len(current) >= MAX_CUE_WORDS:
            groups.append(current)
            current = []
    if current:
        groups.append(current)

    per_word = duration / len(words)
    out, spoken = [], 0
    for group in groups:
        start = spoken * per_word
        spoken += len(group)
        out.append((start, spoken * per_word, " ".join(group)))
    return out


def build_cues(scenes, offsets):
    """Flattens every scene into (start, end, text) on the finished timeline.

    offsets[i] is the global start of scene i, which already accounts for the
    crossfades — consecutive scenes overlap by the crossfade duration, so cues
    are clipped against their successor at the end to keep the track monotonic.
    """
    cues = []
    for idx, scene in enumerate(scenes):
        if idx >= len(offsets):
            break
        if scene.get("is_outro"):
            # The transcript describes THIS video. The outro advertises the
            # next one, and indexing its subject here would compete with that
            # episode's own transcript for the same search terms.
            continue
        offset = offsets[idx]
        duration = float(scene.get("duration") or 0)
        words = scene.get("words") or []

        local = (_group_timed_words(words) if words
                 else _group_untimed(scene.get("narration", ""), duration))
        for start, end, text in local:
            cues.append((offset + start, offset + end, _clean(text)))

    cues = [c for c in cues if c[2]]
    cues.sort(key=lambda c: c[0])

    # A cue may not outlive the next one's start, or the track stutters.
    clipped = []
    for i, (start, end, text) in enumerate(cues):
        if i + 1 < len(cues):
            end = min(end, cues[i + 1][0])
        if end - start < 0.05:      # too short to read; drop rather than flash
            continue
        clipped.append((start, end, text))
    return clipped


def _clean(text: str) -> str:
    """Collapses whitespace and drops the caption-only markup. The narration is
    written for the ear, so it needs no other repair."""
    return re.sub(r"\s+", " ", text).replace("{", "").replace("}", "").strip()


def build_srt(scenes, offsets) -> str:
    cues = build_cues(scenes, offsets)
    blocks = []
    for i, (start, end, text) in enumerate(cues, start=1):
        blocks.append(f"{i}\n{_srt_time(start)} --> {_srt_time(end)}\n{text}\n")
    return "\n".join(blocks)


def write_srt(scenes, offsets, build_dir: str):
    """Writes the track and returns its path, or None when there is nothing to
    write. Never raises: a missing transcript must not fail a finished render."""
    try:
        srt = build_srt(scenes, offsets)
        if not srt.strip():
            print("⚠️ No caption cues produced — skipping the transcript.")
            return None
        path = os.path.join(build_dir, SRT_NAME)
        with open(path, "w", encoding="utf-8") as f:
            f.write(srt)
        print(f"🗒️  Transcript written: {srt.count('-->')} cues → {SRT_NAME}")
        return path
    except Exception as e:
        print(f"⚠️ Could not build the transcript ({type(e).__name__}: {e}).")
        return None
