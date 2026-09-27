"""
Audio engine. Three tiers, tried in this order:

    1. Kokoro TTS         — local, offline, always tried first once the
                             'kokoro' package is installed. No API key, no
                             signup, no card, no rate limit, no daily quota —
                             it runs inside this same job. Apache-2.0
                             licensed (hexgrad/Kokoro-82M), so it is safe for
                             a channel that plans to monetize; several of the
                             free CLOUD alternatives looked at before this
                             are not (see below).
    2. Azure Speech        — AZURE_SPEECH_KEY + AZURE_SPEECH_REGION. Davis
                             Multilingual, real SSML express-as styles, full
                             word-boundary timing. Opt-in, F0-tier-only by
                             design (hard-capped, never billed).
    3. Microsoft Edge TTS  — always available, no key, no styles, but DOES
                             give full word-boundary timing. What this file
                             does if nothing above is configured, or if the
                             tiers above fail.

GEMINI TTS WAS REMOVED (2026-09-20). Two real, load-bearing problems with it,
not a preference:

  - QUOTA WALL ON LONG-FORM. Google's own error on a real run named the exact
    limit: "generate_content_free_tier_requests, limit: 10" per minute for
    gemini-2.5-flash-tts. A long-form video makes 40+ calls; every retry on a
    429 ALSO burns one of those 10, so retrying digs the hole deeper instead
    of recovering from it. No pacing number fixed this — 21s and 30s both
    failed on real runs, at different scene numbers, because the wall is a
    request-count ceiling, not a timing problem. Every long-form video that
    hit it discarded its Gemini audio entirely and rebuilt on edge-tts, which
    means Gemini was never actually the voice long-form videos published
    with; it was 15-20 minutes of wasted retries before every one of them.
  - TONE DRIFT ON SHORTS. Shorts fit under the quota and finished on Gemini
    every time, but each scene is a fully independent call with no numeric
    pitch/rate control — only a natural-language instruction like "say this
    calmly" — and no continuity between calls. The model's own delivery
    audibly drifted scene to scene within a single video.

Before landing on Kokoro, the free/no-card cloud alternatives were checked
directly, not assumed: Z.ai/Zhipu has no productized TTS (their "Voice
Models" tab is speech-TO-text, not synthesis); MiniMax has no free tier at
all; Fish Audio's free tier is real and good but its terms restrict output to
"personal, non-commercial projects" — a real problem for a channel that
intends to monetize, not just a technicality; Groq's PlayAI-TTS is free and
already uses a key this pipeline already has, but returns no word-level
timing and no documented style control (the same caption/style regression
Gemini already had), and its free-tier commercial-use terms are ambiguous in
Groq's own docs. Kokoro is the only option that is simultaneously free
forever, requires no signup of any kind, carries no commercial-use
restriction (Apache 2.0), and removes the quota wall entirely by not making
a network call at all.

KOKORO_VOICE picks the voice (default "am_onyx"). The three workflows pin it
differently. The long-form lanes started as a deliberate 1-2 week A/B
comparison and were then swapped to the owner's explicit final choice
(not a data-driven pick either way):
    weekly_longform.yml (Encore lane)   -> am_santa
    weekly_timely.yml   (Trending lane) -> am_onyx
    daily_publish.yml   (Shorts)        -> alternates am_onyx/am_santa by
                                            run, via KOKORO_VOICE_ROTATION —
                                            see main.py.

Kokoro has no word-boundary API (it yields graphemes/phonemes/audio per
chunk, not timestamps) — same gap Gemini TTS had. Unlike Gemini, this is no
longer a silent gap: _estimate_word_timings() below approximates per-word
start/end from letter counts and punctuation pauses, so captions still get
real per-word chunks instead of the old two-halves-of-the-scene fallback.
It is an estimate, not measured alignment, but it fixed the "captions don't
track what's being said" complaint that shipped once Kokoro (with its 0
real word timings) became the primary engine every video actually uses,
instead of only the occasional edge-tts fallback Gemini's quota wall used
to force most long-form videos onto anyway.

GEMINI vs AZURE FAILURE HANDLING WERE DELIBERATELY DIFFERENT (Azure has an
edge-tts voice that matches its character, so falling back scene-by-scene
was a small jump; Gemini's voices shared no lineage with edge-tts's, so it
failed atomically instead). Kokoro follows the AZURE shape, not the old
Gemini one: it has no rate-limit wall to protect against and no reason a
mid-video failure is more likely than a first-scene one, so a failure simply
falls back to Azure-or-edge-tts for the rest of the run, the same as Azure's
own failure handling already does.
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


def _estimate_word_timings(text: str, duration: float):
    """Approximates per-word start/end for an engine that gives no real
    timing (Kokoro and the gTTS emergency fallback both land here).

    Not true alignment — there is no audio analysis — but weighting each
    word by its letter count, with an added pause after clause and sentence
    ends, tracks natural speech rhythm far better than the two fallbacks
    this replaced: splitting the whole sentence into two blind halves held
    for the scene's duration (video_engine's old no-timings case), or
    spreading words evenly regardless of length (equal screen time for "a"
    and "chrysanthemum"). Good enough for the chunk-level granularity
    captions actually render at.
    """
    words = (text or "").split()
    if not words or duration <= 0:
        return []

    def _weight(word):
        letters = len(re.sub(r"[^\w]", "", word, flags=re.UNICODE))
        stripped = word.rstrip()
        pause = (3.0 if stripped.endswith((".", "!", "?"))
                 else 1.2 if stripped.endswith((",", ";", ":", "—")) else 0.0)
        return max(letters, 1) + pause

    weights = [_weight(w) for w in words]
    total = sum(weights)

    out, spent = [], 0.0
    for word, w in zip(words, weights):
        start = spent / total * duration
        spent += w
        out.append({"word": word, "start": start, "end": spent / total * duration})
    return out

# Deep, calm British male — fits the dark-documentary tone.
# Other strong options: en-US-AndrewMultilingualNeural (warm US male),
# en-GB-SoniaNeural (female), en-US-BrianMultilingualNeural.
# Preview any voice with:  edge-tts --list-voices
VOICE = os.getenv("TTS_VOICE", "en-GB-RyanNeural")
RATE = os.getenv("TTS_RATE", "+8%")    # slightly faster = better Shorts retention
PITCH = os.getenv("TTS_PITCH", "-4Hz")  # slightly deeper = more gravitas

# ---------------------------------------------------------------------------
# Kokoro TTS — local, offline, top priority
#
# See the module docstring for why this replaced Gemini TTS. hexgrad/Kokoro-
# 82M, Apache 2.0. Picked by ear: https://huggingface.co/spaces/hexgrad/
# Kokoro-TTS lets you type real narration and compare voices directly.
#
# Runs through the 'kokoro-onnx' package (ONNX Runtime), not the reference
# 'kokoro' package — tested both directly before choosing. The reference
# package pulls in full PyTorch plus bundled CUDA wheels even though this
# job only ever runs on a CPU: a clean install measured 5.9 GB. kokoro-onnx
# measured 199 MB installed, produces identical audio (same underlying
# weights), and generated a test clip at RTF≈0.25 (4x faster than real
# time) on an ordinary CI CPU core — a 5.9 GB dependency for a GitHub
# Actions job with single-digit GB of free disk was never going to be
# survivable, especially alongside a long-form run's rendered images/video.
# ---------------------------------------------------------------------------

KOKORO_VOICE = os.getenv("KOKORO_VOICE", "am_onyx")

# Downloaded once by the "Download Kokoro model" workflow step (cached
# between runs — see the workflow YAMLs) rather than fetched by this code,
# so a transient download failure shows up as an explicit, named CI step
# instead of surfacing as a mysterious FileNotFoundError mid-render.
KOKORO_MODEL_PATH = os.getenv("KOKORO_MODEL_PATH", "models/kokoro-v1.0.onnx")
KOKORO_VOICES_PATH = os.getenv("KOKORO_VOICES_PATH", "models/voices-v1.0.bin")

# Built once (loads the ~325MB ONNX model) and reused for every scene in the
# run rather than once per scene — construction took ~1s in testing, trivial
# next to a whole video, but there is no reason to pay it 40+ times.
_kokoro_instance = None

# If Kokoro fails once, every remaining scene in this run uses Azure/edge-tts
# instead — never Kokoro again for scenes 1..N and something else for the
# rest, which would put two different voices in one video. Module-level
# because the pipeline calls _synthesize once per scene, not once per video.
_kokoro_failed_this_run = False


def _kokoro_instance_get():
    global _kokoro_instance
    from kokoro_onnx import Kokoro  # imported lazily: optional dependency
    if _kokoro_instance is None:
        _kokoro_instance = Kokoro(KOKORO_MODEL_PATH, KOKORO_VOICES_PATH)
    return _kokoro_instance


def _kokoro_speed(rate: str) -> float:
    """Converts this file's edge-tts-style "+8%"/"-4%" rate string into the
    float multiplier Kokoro's speed parameter wants (1.0 = normal, 1.08 =
    8% faster). Anything that doesn't parse as a percentage is left at 1.0
    rather than raising — a scene's speed setting should never be able to
    break synthesis."""
    match = re.match(r"^([+-]?\d+(?:\.\d+)?)%$", (rate or "").strip())
    if not match:
        return 1.0
    return 1.0 + float(match.group(1)) / 100.0


def _kokoro_lang(voice: str) -> str:
    """Kokoro's own naming convention: the first letter of the voice name is
    its locale ('a' = American English, 'b' = British, ...), so the right
    phonemizer language follows directly from whichever voice was asked for
    instead of needing its own separate setting to stay in sync."""
    return {"a": "en-us", "b": "en-gb"}.get((voice or "")[:1], "en-us")


def _synthesize_kokoro(text: str, output_path: str, voice: str = None,
                       rate: str = None):
    """Kokoro-82M, running locally inside this job via ONNX Runtime.

    Kokoro has no word-boundary API (it yields audio per chunk, not
    timestamps), so the word list returned here is estimated by
    _estimate_word_timings rather than measured — see that function's
    docstring. The estimate is built from the raw sample count before the
    ffmpeg re-encode below, i.e. the same untrimmed-audio timeline real
    engines report into, so it composes correctly with the caller's later
    silence-trim shift.

    Output is re-encoded to real mp3 rather than left as WAV-under-a-.mp3-
    extension, so every engine's output is identical in format, not just in
    filename.
    """
    import soundfile as sf

    voice = voice or KOKORO_VOICE
    samples, sample_rate = _kokoro_instance_get().create(
        text, voice=voice, speed=_kokoro_speed(rate), lang=_kokoro_lang(voice))
    duration = len(samples) / float(sample_rate)

    tmp_wav = output_path + ".kokoro.wav"
    sf.write(tmp_wav, samples, sample_rate)
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
             "-i", tmp_wav, "-q:a", "2", output_path],
            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, check=True)
    finally:
        if os.path.exists(tmp_wav):
            os.remove(tmp_wav)

    global _last_engine
    _last_engine = "kokoro"
    try:
        words = _align_kokoro(text, samples, sample_rate, _kokoro_lang(voice))
        if words and len(words) == len(text.split()):
            return words
    except Exception as e:
        print(f"   ⚠️ Pause alignment failed ({type(e).__name__}: {e}) — estimating.")
    return _estimate_word_timings(text, duration)


_last_engine = None


_PUNCT_END = (",", ";", ":", ".", "!", "?", "—", "–")


def _align_kokoro(text, samples, sr, lang):
    """Word timings for a Kokoro take, anchored on its real pauses.

    Kokoro reports no timings, and a letter-count estimate drifts by up to a
    second over a long sentence, which put cast-card freezes and date-ruler
    landings visibly off the spoken word. Kokoro pauses at every comma and
    full stop; those pauses are found in the audio itself and pinned to the
    punctuation that caused them, the speech start and end are pinned the
    same way, and inside each clause the words share the time by how many
    sounds (phonemes) they have. docs/VIDEO_SPEC.md 10."""
    import numpy as np
    tokens = (text or "").split()
    if not tokens:
        return []
    x = np.abs(np.asarray(samples, dtype=np.float32))
    hop = max(1, int(sr * 0.01))
    n = len(x) // hop
    if n < 5:
        return _estimate_word_timings(text, len(x) / float(sr))
    env = x[: n * hop].reshape(n, hop).mean(axis=1)
    thr = max(1e-4, float(np.percentile(env, 99)) * 0.04)
    loud = env > thr
    idx = np.nonzero(loud)[0]
    if not len(idx):
        return _estimate_word_timings(text, len(x) / float(sr))
    s0, s1 = idx[0] * 0.01, (idx[-1] + 1) * 0.01
    gaps, run = [], None
    for k in range(idx[0], idx[-1] + 1):
        if not loud[k]:
            run = k if run is None else run
        elif run is not None:
            if k - run >= 7:                       # 70 ms or more of silence
                gaps.append((run * 0.01, k * 0.01))
            run = None

    tok = None
    try:
        tok = _kokoro_instance_get().tokenizer
    except Exception:
        pass

    def weight(w):
        core = re.sub(r"[^\w'-]", "", w)
        if tok is not None and core:
            try:
                return max(1, len(tok.phonemize(core, lang).replace(" ", "")))
            except Exception:
                pass
        return max(1, len(core))

    weights = [weight(w) for w in tokens]
    bounds = [j for j, w in enumerate(tokens[:-1]) if w.rstrip("\"')").endswith(_PUNCT_END)]
    total = float(sum(weights))
    speech = s1 - s0 - sum(b - a for a, b in gaps)
    # expected boundary times from the weights, then match real pauses in order
    anchors = [(0, s0, None)]
    gi = 0
    cum = 0.0
    for j in range(len(tokens) - 1):
        cum += weights[j]
        if j not in bounds:
            continue
        expect = s0 + speech * cum / total + sum(b - a for a, b in gaps[:gi])
        best = None
        for g in range(gi, len(gaps)):
            mid = (gaps[g][0] + gaps[g][1]) / 2
            if abs(mid - expect) <= 0.9 and (best is None or abs(mid - expect) < abs(
                    (gaps[best][0] + gaps[best][1]) / 2 - expect)):
                best = g
            if mid - expect > 0.9:
                break
        if best is not None:
            anchors[-1] = anchors[-1][:2] + (gaps[best][0],)
            anchors.append((j + 1, gaps[best][1], None))
            gi = best + 1
    anchors[-1] = anchors[-1][:2] + (s1,)
    out = [None] * len(tokens)
    for a, (i0, t0, t1) in enumerate(anchors):
        i1 = anchors[a + 1][0] if a + 1 < len(anchors) else len(tokens)
        span_w = float(sum(weights[i0:i1])) or 1.0
        t = t0
        for k in range(i0, i1):
            d = (t1 - t0) * weights[k] / span_w
            out[k] = {"word": tokens[k], "start": round(t, 3), "end": round(t + d, 3)}
            t += d
    return out


# ---------------------------------------------------------------------------
# Azure Speech — second priority when configured
#
# Chosen voice: en-US-DavisMultilingualNeural. Unlike edge-tts, Azure's real
# Speech SDK accepts SSML <mstts:express-as style="..."> — edge-tts explicitly
# cannot (Microsoft strips any SSML shape it did not generate itself; see
# rany2/edge-tts#426), which is the entire reason this file did not support
# per-scene emotional styles before. Davis Multilingual's style list, read
# live off Azure's own voice gallery: Default, Empathetic, Funny, Relieved.
#
# Detection of "the free quota ran out" is reactive, not date-based: try the
# call, and if it fails, fall back for the rest of THIS run — never per
# scene. Falling back mid-video would mix Davis's styled delivery with a
# different engine's flat one inside a single video, which is a bigger
# tonal jump than losing the styles. See _synthesize() below.
# ---------------------------------------------------------------------------

AZURE_SPEECH_KEY = os.getenv("AZURE_SPEECH_KEY", "")
AZURE_SPEECH_REGION = os.getenv("AZURE_SPEECH_REGION", "")
AZURE_ENABLED = bool(AZURE_SPEECH_KEY and AZURE_SPEECH_REGION)

AZURE_VOICE = os.getenv("AZURE_VOICE", "en-US-DavisMultilingualNeural")

# Styles this voice actually supports, per Azure's voice gallery. Anything
# else passed as scene["tts_style"] is treated as no style rather than sent
# to the API — an unsupported style name is a hard SSML error on Azure,
# not a warning, and one bad style value should not cost a whole scene.
AZURE_STYLES = {"empathetic", "funny", "relieved"}

# If Azure fails once, every remaining scene in this run uses edge-tts —
# never Azure again for scenes 1..N and edge-tts for the rest, which would
# put two different voices in one video. Module-level because the pipeline
# calls _synthesize once per scene, not once per video.
_azure_failed_this_run = False

# When the fallback fires, edge-tts stands in for Davis specifically — not
# the channel's normal default voice (Ryan). Falling all the way back to a
# different accent and gender mid-project is a bigger jump than losing the
# styles; en-US-DavisNeural is edge-tts's own Davis, sans styles, same
# character.
EDGE_FALLBACK_VOICE = os.getenv("EDGE_FALLBACK_VOICE", "en-US-DavisNeural")


def _build_ssml(text: str, style: str = None, rate: str = None) -> str:
    """SSML for Azure's real Speech SDK — the shape edge-tts is forbidden
    from accepting, which is the whole reason styles live here."""
    import xml.sax.saxutils as saxutils
    escaped = saxutils.escape(text)
    rate_attr = f' rate="{rate}"' if rate else ""
    body = f'<prosody{rate_attr}>{escaped}</prosody>'
    style = (style or "").strip().lower()
    if style in AZURE_STYLES:
        body = (f'<mstts:express-as style="{style}">{body}</mstts:express-as>')
    return (
        '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
        'xmlns:mstts="https://www.w3.org/2001/mstts" xml:lang="en-US">'
        f'<voice name="{AZURE_VOICE}">{body}</voice></speak>'
    )


def _synthesize_azure(text: str, output_path: str, style: str = None,
                      rate: str = None):
    """Azure Speech SDK synthesis. Raises on any failure — quota exhaustion,
    auth, network — so the caller's fallback can catch it uniformly; this
    function does not try to distinguish failure reasons, because the
    response to all of them is the same: stop using Azure for this run."""
    import azure.cognitiveservices.speech as speechsdk

    speech_config = speechsdk.SpeechConfig(subscription=AZURE_SPEECH_KEY,
                                           region=AZURE_SPEECH_REGION)
    audio_config = speechsdk.audio.AudioOutputConfig(filename=output_path)
    synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config,
                                              audio_config=audio_config)

    words = []

    def _on_word(evt):
        start = evt.audio_offset / 10_000_000  # 100-ns units -> seconds
        # Azure's word-boundary event carries duration in the same 100-ns
        # units on recent SDKs; older ones expose it as a timedelta. Cover
        # both rather than pin an SDK version this file does not control.
        dur = getattr(evt, "duration", None)
        dur_s = dur.total_seconds() if hasattr(dur, "total_seconds") \
            else (dur / 10_000_000 if dur else 0.0)
        words.append({"word": evt.text, "start": start, "end": start + dur_s})

    synthesizer.synthesis_word_boundary.connect(_on_word)

    ssml = _build_ssml(text, style=style, rate=rate)
    result = synthesizer.speak_ssml_async(ssml).get()

    if result.reason != speechsdk.ResultReason.SynthesizingAudioCompleted:
        detail = getattr(result, "cancellation_details", None)
        reason = f"{detail.reason}: {detail.error_details}" if detail else result.reason
        raise RuntimeError(f"Azure Speech did not complete: {reason}")

    return words


def _synthesize(text: str, output_path: str, rate: str = None, style: str = None,
                voice: str = None):
    """Generates the MP3 and returns word timings: [(word, start_s, end_s), ...].

    Tries Kokoro first (local, no signup, no rate limit), then Azure if
    configured, then edge-tts — falling back for the rest of the run on any
    tier's failure, never per scene, so one video never mixes voices
    mid-way through. See the module docstring for why Kokoro sits ahead of
    Azure: it is the voice actually chosen for this channel, so a failure
    in it should degrade gracefully rather than silently hand narration to
    a different configured engine scene by scene.

    rate overrides the global narration speed for this scene. voice
    overrides KOKORO_VOICE for this call only — see KOKORO_VOICE_ROTATION in
    main.py, which alternates it per run for the Shorts A/B test. style is
    an Azure express-as style (see AZURE_STYLES); Kokoro and edge-tts both
    silently ignore it, since neither can accept styles at all.
    """
    global _azure_failed_this_run, _kokoro_failed_this_run

    if not _kokoro_failed_this_run:
        try:
            return _synthesize_kokoro(text, output_path, voice=voice, rate=rate)
        except Exception as e:
            next_engine = "Azure" if AZURE_ENABLED else "edge-tts"
            print(f"⚠️ Kokoro TTS failed ({type(e).__name__}: {e}) — falling "
                  f"back to {next_engine} for the rest of this run.")
            _kokoro_failed_this_run = True

    if AZURE_ENABLED and not _azure_failed_this_run:
        try:
            return _synthesize_azure(text, output_path, style=style, rate=rate)
        except Exception as e:
            print(f"⚠️ Azure Speech failed ({e}) — falling back to edge-tts "
                  f"for the rest of this run.")
            _azure_failed_this_run = True

    fallback_voice = EDGE_FALLBACK_VOICE if AZURE_ENABLED else VOICE
    return asyncio.run(_synthesize_edge(text, output_path, rate=rate,
                                        voice=fallback_voice))


async def _synthesize_edge(text: str, output_path: str, rate: str = None,
                           voice: str = None):
    communicate = edge_tts.Communicate(text, voice or VOICE, rate=rate or RATE,
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
    pipeline survives. gTTS gives no word-boundary API either, so the word
    list is estimated the same way Kokoro's is — see _estimate_word_timings."""
    from gtts import gTTS

    gTTS(text=text, lang="en", tld="co.uk").save(output_path)
    return _estimate_word_timings(text, get_audio_duration(output_path))


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


def _run_audio_pass(scenes: list, output_dir: str, voice: str = None):
    """One full pass over every scene, synthesizing each through _synthesize()
    (Kokoro, falling back to Azure/edge-tts, with a per-scene gTTS emergency
    fallback if every configured engine fails on one scene).

    voice overrides KOKORO_VOICE for this whole video — see
    KOKORO_VOICE_ROTATION in main.py for the Shorts A/B test that uses this.
    """
    skipped, trimmed_total = 0, 0.0
    for idx, scene in enumerate(scenes):
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
        style = scene.get("tts_style")
        style_note = f", style={style}" if style else ""
        print(f"🗣️ Synthesizing scene {idx} at {rate}{style_note}: "
              f"'{text[:40]}...'")

        try:
            words = _synthesize(text, audio_path, rate=rate, style=style,
                                voice=voice)
            if os.path.getsize(audio_path) < 1000:  # suspiciously small = failed
                raise RuntimeError("TTS produced an empty file")
        except Exception as e:
            print(f"⚠️ TTS failed ({e}) — falling back to gTTS for this scene.")
            try:
                words = _synthesize_fallback(text, audio_path)
            except Exception as e2:
                # Every configured engine gone for this scene. Every other
                # scene still has a video to be part of.
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

    return skipped, trimmed_total


def generate_audio_pipeline(script_data: dict, output_dir: str = "build",
                            voice: str = None) -> dict:
    """voice overrides KOKORO_VOICE for this whole video. Passed by main.py
    for the Shorts KOKORO_VOICE_ROTATION A/B test; longform_main.py leaves it
    unset and gets whichever voice that workflow's KOKORO_VOICE env var pins."""
    scenes = script_data.get("scenes", [])
    os.makedirs(output_dir, exist_ok=True)

    engine_note = f"Kokoro ({voice or KOKORO_VOICE})"
    engine_note += (f", falling back to Azure ({AZURE_VOICE}) then edge-tts"
                    if AZURE_ENABLED else f", falling back to edge-tts ({VOICE})")
    print(f"🎙️ Initializing Audio Engine ({engine_note})...")

    skipped, trimmed_total = _run_audio_pass(scenes, output_dir, voice=voice)

    note = f" ({skipped} scene(s) skipped)" if skipped else ""
    if trimmed_total > 0.5:
        print(f"✂️  Removed {trimmed_total:.1f}s of dead air across the video.")
    print(f"✅ Audio pipeline complete{note}.")
    return script_data


if __name__ == "__main__":
    test = {"scenes": [{"narration": "In the winter of 1914, an entire army vanished without firing a single shot."}]}
    out = generate_audio_pipeline(test)
    print(out["scenes"][0]["words"][:5])
