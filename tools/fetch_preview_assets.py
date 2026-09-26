"""
Fetch the raw material for a rhythm preview: real archive photos and
narration lines for one story, so a preview render can be built and iterated
anywhere, without the archives or the voice model.

Runs in CI (.github/workflows/preview_assets.yml), where the archives are
reachable and the Kokoro model is cached; the workflow pushes the output to
the `preview-assets` branch.

    python tools/fetch_preview_assets.py preview_out
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from archive_engine import search_archives, download_archival_image, format_credit
from audio_engine import _synthesize_kokoro, get_audio_duration

# The Kinmen video's own story, 23 August 1958.
QUERIES = [
    "Kinmen 1958", "Mao Zedong 1958", "Fujian coast artillery",
    "Chinese artillery 1958", "Kinmen bombardment", "Kinmen bunker",
    "Taiwan Strait Crisis 1958", "USS Midway 1958", "Seventh Fleet 1958",
    "Dwight Eisenhower 1958", "Chiang Kai-shek Kinmen",
]
PER_QUERY = 2

LINES = [
    "In the summer of 1958, the plan in Beijing looked simple.",
    "Shell the islands of Kinmen, cut them off from Taiwan, and watch "
    "Washington hesitate.",
    "Along the Fujian coast, hundreds of guns waited for a single word.",
    "On the twenty-third of August, Mao Zedong gave it.",
    "Within hours, tens of thousands of shells had fallen on Kinmen.",
    "And Washington did not hesitate. It sent the Seventh Fleet.",
    # Two-maps + chain-of-consequences preview (2026-09-26).
    "American warships sailed from their bases in the Philippines into "
    "the Taiwan Strait.",
    "The shelling drew the Seventh Fleet into the strait. American escorts "
    "broke the blockade, and in October, Beijing called a ceasefire.",
]


def main(out):
    img_dir = os.path.join(out, "images")
    os.makedirs(img_dir, exist_ok=True)
    images, seen = [], set()
    for q in QUERIES:
        got = 0
        for c in search_archives(q, limit=8):
            if got >= PER_QUERY or c.get("url") in seen:
                continue
            path = os.path.join(img_dir, f"{len(images):02d}.jpg")
            if download_archival_image(c, path, 1920, 1080):
                seen.add(c.get("url"))
                images.append({"file": f"images/{os.path.basename(path)}",
                               "query": q, "credit": format_credit(c)})
                got += 1
        print(f"{q!r}: {got} image(s)")

    voice = os.getenv("KOKORO_VOICE", "am_onyx")
    lines = []
    for i, text in enumerate(LINES):
        path = os.path.join(out, f"line{i}.mp3")
        _synthesize_kokoro(text, path, voice=voice)
        lines.append({"file": os.path.basename(path), "text": text,
                      "duration": get_audio_duration(path)})
        print(f"line {i}: {lines[-1]['duration']:.1f}s")

    with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"images": images, "lines": lines, "voice": voice}, f,
                  indent=2, ensure_ascii=False)
    print(f"{len(images)} images, {len(lines)} lines -> {out}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "preview_out")
