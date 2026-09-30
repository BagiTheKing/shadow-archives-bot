"""
Photos for designing a thumbnail by hand: each person's free Wikipedia
portrait (dossier_engine.person_file) and a few archive images per query,
with their credits. Runs in CI (.github/workflows/thumb_assets.yml), where
Wikipedia and Commons are reachable; the output goes to the `thumb-assets`
branch.

    PEOPLE="A|B" QUERIES="x|y" python tools/fetch_thumb_assets.py out
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from archive_engine import download_archival_image, format_credit, search_archives
from dossier_engine import person_file


def main(out):
    people_names = [p.strip() for p in os.getenv("PEOPLE", "").split("|") if p.strip()]
    queries = [q.strip() for q in os.getenv("QUERIES", "").split("|") if q.strip()]
    per_query = int(os.getenv("PER_QUERY", "4"))
    people, images = [], []
    for i, name in enumerate(people_names):
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
        print(f"{name}: {'portrait' if info else 'no free portrait'}")
    img_dir = os.path.join(out, "images")
    os.makedirs(img_dir, exist_ok=True)
    seen = set()
    for q in queries:
        got = 0
        for c in search_archives(q, limit=12):
            if got >= per_query or c.get("url") in seen:
                continue
            path = os.path.join(img_dir, f"{len(images):02d}.jpg")
            if download_archival_image(c, path, 1920, 1080):
                seen.add(c.get("url"))
                images.append({"file": os.path.relpath(path, out), "query": q,
                               "credit": format_credit(c)})
                got += 1
        print(f"{q!r}: {got} image(s)")
    with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"people": people, "images": images}, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "thumb_out")
