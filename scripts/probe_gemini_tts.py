"""
One-off diagnostic probe for Gemini's native TTS — NOT part of the pipeline.

Answers two questions before any real integration code gets written:
1. Does the response carry any word/segment timing data (needed for captions)?
2. Does a natural-language style instruction ("dry, deadpan") actually change
   what comes back, structurally at least — full judgment of the audio itself
   still needs a human ear, which is why both WAVs are uploaded as artifacts
   rather than judged here.

Run via .github/workflows/probe_gemini_tts.yml (workflow_dispatch only).
Delete this file and its workflow once the integration decision is made —
it is a probe, not a permanent part of the pipeline.
"""

import base64
import json
import os
import sys

import requests

API_KEY = os.environ["GEMINI_API_KEY"]
MODEL = os.environ.get("PROBE_MODEL", "gemini-2.5-flash-preview-tts")
URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"

CASES = [
    ("neutral", "Say in a calm, factual, forensic documentary voice: "
                "Three separate committees approved the design. Nobody "
                "re-measured the tolerance before it shipped."),
    ("dry_joke", "Say this as a single dry, deadpan, ironic aside — a beat "
                "of dark humor, not a cheerful or comedic voice: Three "
                "separate committees approved the design. Nobody "
                "re-measured the tolerance before it shipped."),
]


def probe(label: str, text: str):
    print(f"\n=== {label} ===")
    body = {
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": "Kore"}}
            },
        },
    }
    r = requests.post(URL, params={"key": API_KEY}, json=body, timeout=60)
    print(f"HTTP {r.status_code}")
    if r.status_code != 200:
        print(r.text[:2000])
        return

    data = r.json()

    # Print the full response shape with the audio payload itself redacted —
    # that is the actual question: what OTHER fields exist alongside it.
    def redact(obj):
        if isinstance(obj, dict):
            out = {}
            for k, v in obj.items():
                if k == "data" and isinstance(v, str) and len(v) > 100:
                    out[k] = f"<{len(v)} base64 chars redacted>"
                else:
                    out[k] = redact(v)
            return out
        if isinstance(obj, list):
            return [redact(v) for v in obj]
        return obj

    print("Response shape (audio payload redacted):")
    print(json.dumps(redact(data), indent=2)[:4000])

    # Pull out whatever audio came back and save it so a human can listen.
    try:
        parts = data["candidates"][0]["content"]["parts"]
        audio_part = next(p for p in parts if "inlineData" in p)
        mime = audio_part["inlineData"]["mimeType"]
        raw = base64.b64decode(audio_part["inlineData"]["data"])
        print(f"Audio: {mime}, {len(raw)} bytes")

        out_path = f"build/probe_{label}.raw"
        os.makedirs("build", exist_ok=True)
        with open(out_path, "wb") as f:
            f.write(raw)
        print(f"Wrote {out_path}")

        # Gemini TTS returns raw PCM (audio/L16;rate=24000, typically) with
        # no container — wrap it in a minimal WAV header so it is actually
        # playable, rather than making the human do that by hand.
        if mime.startswith("audio/L16") or mime.startswith("audio/pcm"):
            rate = 24000
            for token in mime.split(";"):
                if token.strip().startswith("rate="):
                    rate = int(token.strip().split("=")[1])
            wav_path = f"build/probe_{label}.wav"
            _write_wav(wav_path, raw, rate)
            print(f"Wrote {wav_path} ({rate} Hz)")
    except Exception as e:
        print(f"Could not extract audio: {type(e).__name__}: {e}")

    # The actual question this probe exists to answer.
    blob = json.dumps(data)
    hits = [kw for kw in ("timepoint", "wordTiming", "alignment", "mark",
                          "offset", "duration", "timestamp")
            if kw.lower() in blob.lower()]
    print(f"Timing-related keys found anywhere in response: {hits or 'NONE'}")


def _write_wav(path, pcm_bytes, sample_rate, channels=1, sample_width=2):
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


if __name__ == "__main__":
    if "GEMINI_API_KEY" not in os.environ:
        print("GEMINI_API_KEY not set.")
        sys.exit(1)
    for label, text in CASES:
        probe(label, text)
