"""
Builds library/<slug>/entry.json from a Media Catalog result, so the weekly
session only writes the choices (handbook, Option 3).

    python tools/library_add.py catalog_dir spec.json

spec.json is an entry (see library/budapest-1994/entry.json) except that
`then` and every image is {"id": "P14", "desc": "U-2 photo of a missile site,
14 October 1962", "focus": [x, y]} instead of url/credit: the url and the
credit (desc — artist — licence) come from catalog_dir/catalog.json.
"""
import json
import os
import re
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "library")


def strip_html(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s or "")).strip()


def resolve(pic, cat):
    c = cat[pic["id"]]
    url = (c.get("play") if c.get("kind") == "video" else None) or c["url"]
    url = url.split("?")[0]
    if url.lower().endswith((".tif", ".tiff")):
        raise SystemExit(f"{pic['id']}: a TIFF is too big for the renderer; choose a jpg/png")
    artist = strip_html(c.get("artist"))[:60] or "Unknown author"
    credit = f"{pic['desc']} — {artist} — {c.get('license') or 'Public domain'} (via Wikimedia Commons)"
    out = {"url": url, "credit": credit}
    if "focus" in pic:
        out["focus"] = pic["focus"]
    return out


def main(catdir, spec_path):
    cat = {c["id"]: c for c in json.load(open(os.path.join(catdir, "catalog.json"), encoding="utf-8"))}
    spec = json.load(open(spec_path, encoding="utf-8"))
    spec["then"] = resolve(spec["then"], cat)
    spec["images"] = {k: resolve(v, cat) for k, v in spec["images"].items()}
    out = os.path.join(ROOT, spec["slug"])
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "entry.json"), "w", encoding="utf-8") as f:
        json.dump(spec, f, ensure_ascii=False, indent=1)
    print("wrote", out)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
