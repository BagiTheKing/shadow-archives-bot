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

# Cast-card preview (Six-Day War). Stills are not needed this time; the
# earlier sets are already on the branch history.
QUERIES = []
PER_QUERY = 2
PEOPLE = ["Gamal Abdel Nasser", "Hussein of Jordan", "Moshe Dayan",
          "Nureddin al-Atassi"]

LINES = [
    "In Cairo, Egypt's president, Gamal Abdel Nasser, closed the Straits of "
    "Tiran to Israeli ships.",
    "In Amman, Jordan's King Hussein made his choice. He flew to Cairo.",
    "In Israel, the new defence minister, Moshe Dayan, wanted to strike first.",
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

    from dossier_engine import person_file
    people = []
    for i, name in enumerate(PEOPLE):
        d = os.path.join(out, "people", f"{i:02d}")
        os.makedirs(d, exist_ok=True)
        try:
            info = person_file(name, d)
        except Exception as e:
            info = None
            print(f"{name}: failed ({e})")
        if info:
            info["photo"] = os.path.relpath(info["photo"], out)
            people.append({"name": name, **info})
        print(f"{name}: {'photo + ' + ' · '.join(info['lines']) if info else 'no free portrait'}")

    voice = os.getenv("KOKORO_VOICE", "am_onyx")
    lines = []
    for i, text in enumerate(LINES):
        path = os.path.join(out, f"line{i}.mp3")
        _synthesize_kokoro(text, path, voice=voice)
        lines.append({"file": os.path.basename(path), "text": text,
                      "duration": get_audio_duration(path)})
        print(f"line {i}: {lines[-1]['duration']:.1f}s")

    with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"images": images, "people": people, "lines": lines, "voice": voice}, f,
                  indent=2, ensure_ascii=False)
    print(f"{len(images)} images, {len(lines)} lines -> {out}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "preview_out")
