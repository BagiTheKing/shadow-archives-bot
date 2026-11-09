"""
A review folder for one library entry: its history lines with the entry's own
example today lines, rendered by Paradox Short, to check the pictures by eye
(handbook). NOW is the entry's `then` picture as a stand-in.

    python tools/library_preview.py <slug>      # writes owner_media/_preview/<slug>/
"""
import json
import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
import library_short as L  # noqa: E402


def main(slug):
    e = next(x for x in L.load() if x["slug"] == slug)
    ex = e["examples"]
    spec = {"line1": ex["line1"], "line2": ex["line2"], "now": ex["now"], "now_tag": ex["now_tag"],
            "question": ex["question"], "end_title": e["end_ideas"][0]["title"], "now_person": 0,
            "title": "preview #shorts", "description": "preview", "tags": []}
    short, sources, meta = L.build(e, spec, e["then"]["credit"], e["then"]["url"])
    d = os.path.join(ROOT, "owner_media", "_preview", slug)
    os.makedirs(d, exist_ok=True)
    for n, data in (("short.json", short), ("sources.json", sources)):
        json.dump(data, open(os.path.join(d, n), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(d)


if __name__ == "__main__":
    main(sys.argv[1])
