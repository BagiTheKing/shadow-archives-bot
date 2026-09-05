"""
Audio engine. Three tiers, each optional, tried in this order:

    1. Gemini native TTS   — GEMINI_API_KEY (the same key already used for
                             script generation — no new secret needed).
                             Free-tier eligible, real natural-language style
                             steering, but its API response carries NO
                             word-boundary data at all (checked directly
                             against the raw response, not assumed from
                             docs) — captions degrade to per-scene sync for
                             a video narrated this way.
    2. Azure Speech        — AZURE_SPEECH_KEY + AZURE_SPEECH_REGION. Davis
                             Multilingual, real SSML express-as styles, full
                             word-boundary timing. Opt-in, F0-tier-only by
                             design (hard-capped, never billed).
    3. Microsoft Edge TTS  — always available, no key, no styles, but DOES
                             give full word-boundary timing. What this file
                             does if nothing above is configured, or if the
                             tiers above fail.

Gemini and edge-tts stream (or, for edge, effectively provide) word-boundary
events; where they do, that powers the word-synced captions in
video_engine.py, which is what makes Shorts captions look professional.

GEMINI vs AZURE FAILURE HANDLING ARE DELIBERATELY DIFFERENT. Azure has an
edge-tts voice that matches its character (Davis-to-Davis), so falling back
scene-by-scene for the rest of a run is a small jump. Gemini has no such
matched pairing — its voices and edge-tts's share no lineage — so mixing
them mid-video would be a much bigger, more audible seam than losing styles
ever was. Gemini therefore fails ATOMICALLY: the instant any scene fails,
every Gemini clip already written for this video is discarded and the
ENTIRE video is resynthesized on edge-tts from scratch. A video is either
100% Gemini or 100% edge-tts, never both. See generate_audio_pipeline().
"""

import asyncio
import base64
import os
import re
import subprocess
import time

import edge_tts
import requests

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

# ---------------------------------------------------------------------------
# Gemini native TTS — top priority when configured
#
# Reuses GEMINI_API_KEY outright: same key, same account, same free tier
# already used for script generation. No new signup, no new secret.
#
# Verified live (2026-09-05, model gemini-2.5-flash-preview-tts, voice Kore):
# a plain narration prompt and the same text wrapped in a "say this as a
# single dry, deadpan, ironic aside" instruction produced measurably
# different output (197 vs 187 audio tokens, 378766 vs 359566 bytes) — the
# style instruction does something. The full raw JSON response was searched
# for every timing-related key (timepoint, wordTiming, alignment, mark,
# offset, duration, timestamp): none exist. That is not a documentation gap,
# it is what the API actually returns.
# ---------------------------------------------------------------------------

GEMINI_TTS_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_TTS_MODEL = os.getenv("GEMINI_TTS_MODEL", "gemini-2.5-flash-preview-tts")
# Puck — chosen after listening to the built-in voices live in AI Studio's
# speech playground. The probe below was run against Kore before this voice
# was picked; the mechanism itself is voice-agnostic, so switching the name
# is the only change this needed. Override with GEMINI_TTS_VOICE if that
# ever changes again, no code change required.
GEMINI_TTS_VOICE = os.getenv("GEMINI_TTS_VOICE", "Puck")
GEMINI_TTS_ENABLED = bool(GEMINI_TTS_KEY)
GEMINI_TTS_URL = (f"https://generativelanguage.googleapis.com/v1beta/models/"
                  f"{GEMINI_TTS_MODEL}:generateContent")

# There is no numeric rate/pitch control on this engine — Gemini TTS takes a
# natural-language instruction, not SSML prosody, so style has to be steered
# in words. Only "funny" is actually assigned anywhere in the pipeline today
# (the one joke beat); empathetic/relieved are defined for when that changes,
# mirroring AZURE_STYLES's forward-compatible shape.
_GEMINI_STYLE_INSTRUCTIONS = {
    "funny": "Say this as a single dry, deadpan, ironic aside — a beat of "
             "dark humor, not a cheerful or comedic voice:",
    "empathetic": "Say this gently and empathetically, with real weight:",
    "relieved": "Say this with quiet relief:",
}
_GEMINI_DEFAULT_INSTRUCTION = "Say in a calm, factual, forensic documentary voice:"

# A live run hit a 429 on scene 5 of 47 — five calls into the video, which is
# almost certainly a per-minute rate limit on this preview model, not the
# free tier's actual monthly allowance being spent (those two fail very
# differently: a quota exhaustion response names the exhausted metric, a
# rate limit does not). That run had no way to tell the two apart, because
# _synthesize_gemini used to raise_for_status() without ever reading the
# response body — and it paid for that ambiguity by discarding five good
# clips and resynthesizing the entire video on edge-tts over what a few
# seconds' wait would likely have survived. Retrying a 429 in place costs at
# most a few seconds; treating it as fatal costs the whole reason Gemini was
# worth choosing.
#
# The very next live run proved the first fix was scoped too narrowly: it
# died on scene 1 with a 503 ("This model is currently experiencing high
# demand... Please try again later") — Google's own wording is a more
# explicit retry signal than the 429 case ever was, and it got zero retries
# because only 429 was covered. The Gemini TEXT models hit this exact 503
# shape too (see llm_provider.py's own comments), so it is evidently a
# routine, recurring condition on Google's preview/flash tier generally, not
# a one-off. Every status below is a transient-infrastructure shape, not a
# rejection of the request itself, and gets the same retry treatment; a 4xx
# that is NOT 429 (bad request, auth, not-found) still fails fast, because
# retrying an actually-malformed request just wastes the same three calls
# for the same guaranteed result.
GEMINI_TTS_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
GEMINI_TTS_MAX_ATTEMPTS = 3
GEMINI_TTS_RETRY_DELAYS = (3, 8)  # fallback wait, used only when Google gives no number

# The THIRD live run finally named the actual wall: Google's error body said
# "limit: 3, model: gemini-2.5-flash-tts... Please retry in 59.65s" — three
# calls per roughly a minute. The first five scenes had already gone through
# with no pause between them at all, so scene 5 walked straight into that
# wall. No amount of retrying fixes a rate limit you keep re-triggering the
# instant it clears; the only real fix is to never approach it in the first
# place. So every successful Gemini call is now followed by a deliberate
# pause before the next one — 21s clears three calls per minute with a
# margin, not three calls per minute exactly. This adds real time to a run
# (roughly 15 more minutes across a 47-scene video) in exchange for the
# video actually finishing on Gemini instead of falling back every time.
GEMINI_TTS_PACE_SECONDS = float(os.getenv("GEMINI_TTS_PACE_SECONDS", "21"))

# Google's 429 body names the exact wait when it has one ("Please retry in
# 59.65s") — that number is authoritative and almost always longer than the
# fixed fallback above, so use it whenever the response actually gives it
# rather than guessing.
_RETRY_AFTER_RE = re.compile(r"retry in ([\d.]+)\s*s", re.IGNORECASE)


def _suggested_wait(detail: str, fallback: float) -> float:
    match = _RETRY_AFTER_RE.search(detail or "")
    if match:
        try:
            return float(match.group(1)) + 1.0  # small buffer past what Google asked for
        except ValueError:
            pass
    return fallback


def _write_wav(path: str, pcm_bytes: bytes, sample_rate: int,
              channels: int = 1, sample_width: int = 2):
    """Gemini returns raw PCM with no container. ffmpeg (used two lines
    below to re-encode to the mp3 every other engine already produces) needs
    something with a header to read, so this wraps it in the minimal WAV
    header rather than pulling in a whole audio library for one struct."""
    import struct
    n_samples = len(pcm_bytes)
    with open(path, "wb") as f:
        f.write(b"RIFF")
        f.write(struct.pack("<I", 36 + n_samples))
        f.write(b"WAVE")
        f.write(b"fmt ")
        f.write(struct.pack("<IHHIIHH", 16, 1, channels, sample_rate,
                            sample_rate * channels * sample_width,
                            channels * sample_width, sample_width * 8))
        f.write(b"data")
        f.write(struct.pack("<I", n_samples))
        f.write(pcm_bytes)


def _synthesize_gemini(text: str, output_path: str, style: str = None):
    """Gemini native TTS. Always returns an empty word list — see the module
    docstring on why no timing data exists to return.

    Retries any status in GEMINI_TTS_RETRYABLE_STATUSES (429, and the 5xx
    shapes that mean transient overload rather than a rejected request) up
    to GEMINI_TTS_MAX_ATTEMPTS times with a short pause — see the comments
    above those constants for why both shapes are routine on this preview
    tier and neither means the free allowance is actually spent. Any other
    error, or a retryable one that survives every attempt, raises
    immediately with the actual response body attached: the caller
    (generate_audio_pipeline) decides what THAT means for the whole video,
    not this function, and it needs the real reason to log, not a generic
    HTTP status line.

    Output is re-encoded to real mp3 rather than left as WAV-under-a-.mp3-
    extension, so every engine's output is identical in format, not just in
    filename.
    """
    instruction = _GEMINI_STYLE_INSTRUCTIONS.get((style or "").strip().lower(),
                                                  _GEMINI_DEFAULT_INSTRUCTION)
    body = {
        "contents": [{"parts": [{"text": f"{instruction} {text}"}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {"voiceName": GEMINI_TTS_VOICE}
                },
            },
        },
    }

    last_error = "unknown error"
    for attempt in range(GEMINI_TTS_MAX_ATTEMPTS):
        r = requests.post(GEMINI_TTS_URL, params={"key": GEMINI_TTS_KEY},
                          json=body, timeout=60)

        if r.status_code == 200:
            data = r.json()
            parts = data["candidates"][0]["content"]["parts"]
            audio_part = next(p for p in parts if "inlineData" in p)
            mime = audio_part["inlineData"]["mimeType"]
            raw = base64.b64decode(audio_part["inlineData"]["data"])

            rate_hz = 24000
            for token in mime.split(";"):
                token = token.strip()
                if token.startswith("rate="):
                    rate_hz = int(token.split("=")[1])

            tmp_wav = output_path + ".gemini.wav"
            _write_wav(tmp_wav, raw, rate_hz)
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                     "-i", tmp_wav, "-q:a", "2", output_path],
                    stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, check=True)
            finally:
                if os.path.exists(tmp_wav):
                    os.remove(tmp_wav)

            return []  # confirmed absent from the response — see module docstring

        # Not 200: keep the actual body, not requests' generic "429 Client
        # Error: Too Many Requests" — that alone cannot tell a per-minute
        # rate limit apart from the free tier's allowance actually being
        # spent, and the run that found this bug needed that distinction and
        # did not have it.
        detail = (r.text or "").strip().replace("\n", " ")[:500]
        last_error = f"{r.status_code} {r.reason}: {detail}"

        retryable = r.status_code in GEMINI_TTS_RETRYABLE_STATUSES
        if retryable and attempt < GEMINI_TTS_MAX_ATTEMPTS - 1:
            wait = _suggested_wait(detail, GEMINI_TTS_RETRY_DELAYS[attempt])
            print(f"   ⏳ Gemini TTS returned {r.status_code} (transient — "
                  f"rate limit or temporary overload) — waiting {wait}s and "
                  f"retrying this scene (attempt {attempt + 2}/"
                  f"{GEMINI_TTS_MAX_ATTEMPTS})...")
            time.sleep(wait)
            continue

        raise RuntimeError(last_error)

    raise RuntimeError(last_error)


class GeminiTTSFailed(Exception):
    """Raised the instant any scene fails on Gemini TTS. Caught exactly once,
    in generate_audio_pipeline, which throws away the whole video's Gemini
    output and restarts it entirely on edge-tts — see the module docstring
    for why this engine does not fall back scene by scene the way Azure
    does."""


# ---------------------------------------------------------------------------
# Azure Speech — primary engine when configured
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


def _synthesize(text: str, output_path: str, rate: str = None, style: str = None):
    """Generates the MP3 and returns word timings: [(word, start_s, end_s), ...].

    rate overrides the global narration speed for this scene. The long-form arc
    reads its midpoint slower than its opening on purpose — see PACING in
    longform_llm — and a single global rate made that impossible. style is an
    Azure express-as style (see AZURE_STYLES); edge-tts silently ignores it,
    since it cannot accept styles at all.

    Tries Azure first when configured, falling back to edge-tts for the rest
    of the run on any Azure failure — never per scene, see _azure_failed_this_run.
    """
    global _azure_failed_this_run

    if AZURE_ENABLED and not _azure_failed_this_run:
        try:
            return _synthesize_azure(text, output_path, style=style, rate=rate)
        except Exception as e:
            print(f"⚠️ Azure Speech failed ({e}) — falling back to edge-tts "
                  f"for the rest of this run.")
            _azure_failed_this_run = True

    voice = EDGE_FALLBACK_VOICE if AZURE_ENABLED else VOICE
    return asyncio.run(_synthesize_edge(text, output_path, rate=rate, voice=voice))


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


def _run_audio_pass(scenes: list, output_dir: str, use_gemini: bool):
    """One full pass over every scene, on exactly one engine.

    use_gemini=True calls _synthesize_gemini() directly and raises
    GeminiTTSFailed the INSTANT any scene fails — no per-scene gTTS
    fallback, no continuing to the next scene. That is deliberate: this
    engine's failures are handled one level up, by throwing away the whole
    video's Gemini output and calling this function again with
    use_gemini=False, never by mixing engines within one pass.

    use_gemini=False goes through _synthesize() (which already knows how to
    blend Azure and edge-tts internally, unchanged from before) with its
    existing per-scene gTTS emergency fallback.

    Re-running this for every scene after a Gemini failure is safe without
    any manual cleanup: every scene writes to the same scene_{idx}.mp3 path
    regardless of engine, so a second pass simply overwrites whatever the
    aborted first pass left behind, including for scenes that had already
    succeeded on Gemini before a later one failed.
    """
    # Used only to decide when to skip the post-call pacing pause below —
    # there is no point waiting 21s after the last real Gemini call this
    # pass will ever make.
    last_speakable_idx = None
    if use_gemini:
        for idx, scene in enumerate(scenes):
            if is_speakable(scene.get("narration", "").strip()):
                last_speakable_idx = idx

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
        engine_tag = "gemini" if use_gemini else "standard"
        print(f"🗣️ [{engine_tag}] Synthesizing scene {idx} at {rate}"
              f"{style_note}: '{text[:40]}...'")

        try:
            if use_gemini:
                words = _synthesize_gemini(text, audio_path, style=style)
            else:
                words = _synthesize(text, audio_path, rate=rate, style=style)
            if os.path.getsize(audio_path) < 1000:  # suspiciously small = failed
                raise RuntimeError("TTS produced an empty file")
        except Exception as e:
            if use_gemini:
                # No per-scene recovery here on purpose — see this function's
                # docstring and the module docstring for why.
                raise GeminiTTSFailed(f"scene {idx}: {e}") from e
            print(f"⚠️ TTS failed ({e}) — falling back to gTTS for this scene.")
            try:
                words = _synthesize_fallback(text, audio_path)
            except Exception as e2:
                # Both engines gone for this scene. Every other scene still has
                # a video to be part of.
                print(f"❌ Scene {idx}: gTTS also failed ({type(e2).__name__}: "
                      f"{e2}) — skipping this scene.")
                skipped += 1
                continue

        # Gemini's free tier allows only ~3 calls per minute (see the comment
        # above GEMINI_TTS_PACE_SECONDS) — pause after every successful call
        # except the last one this pass will make, so the next scene never
        # walks straight into the wall the way scene 5 of 47 did on a real
        # run.
        if use_gemini and idx != last_speakable_idx:
            print(f"   ⏳ Pacing Gemini TTS — waiting {GEMINI_TTS_PACE_SECONDS:.0f}s "
                  f"before the next request (free tier allows ~3/min)...")
            time.sleep(GEMINI_TTS_PACE_SECONDS)

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


def generate_audio_pipeline(script_data: dict, output_dir: str = "build") -> dict:
    scenes = script_data.get("scenes", [])
    os.makedirs(output_dir, exist_ok=True)

    if GEMINI_TTS_ENABLED:
        print(f"🎙️ Initializing Audio Engine (voice: {GEMINI_TTS_VOICE} via "
              f"Gemini TTS; if it fails anywhere mid-video, the ENTIRE video "
              f"is redone on edge-tts ({VOICE}) rather than mixing voices)...")
        try:
            skipped, trimmed_total = _run_audio_pass(scenes, output_dir,
                                                     use_gemini=True)
        except GeminiTTSFailed as e:
            print(f"⚠️ Gemini TTS failed ({e}) — discarding every Gemini clip "
                  f"from this video and resynthesizing the ENTIRE thing on "
                  f"edge-tts instead, so it never mixes two different voices.")
            skipped, trimmed_total = _run_audio_pass(scenes, output_dir,
                                                     use_gemini=False)
    else:
        if AZURE_ENABLED:
            print(f"🎙️ Initializing Audio Engine (voice: {AZURE_VOICE} via "
                  f"Azure Speech, styles: {', '.join(sorted(AZURE_STYLES))}; "
                  f"edge-tts fallback: {EDGE_FALLBACK_VOICE})...")
        else:
            print(f"🎙️ Initializing Audio Engine (voice: {VOICE} via edge-tts)...")
        skipped, trimmed_total = _run_audio_pass(scenes, output_dir,
                                                 use_gemini=False)

    note = f" ({skipped} scene(s) skipped)" if skipped else ""
    if trimmed_total > 0.5:
        print(f"✂️  Removed {trimmed_total:.1f}s of dead air across the video.")
    print(f"✅ Audio pipeline complete{note}.")
    return script_data


if __name__ == "__main__":
    test = {"scenes": [{"narration": "In the winter of 1914, an entire army vanished without firing a single shot."}]}
    out = generate_audio_pipeline(test)
    print(out["scenes"][0]["words"][:5])
