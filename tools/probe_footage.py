"""
Checks that real footage can be found and cut for a few queries, without a
full run. Used by .github/workflows/probe_footage.yml.

    python tools/probe_footage.py "Six-Day War" "Gamal Abdel Nasser"
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from archive_engine import (search_commons_video, fetch_commons_video, _clip_seconds,
                            search_archival_video)

ERA = int(os.getenv("PROBE_ERA", "1967"))
os.makedirs("probe_out", exist_ok=True)
for i, q in enumerate(sys.argv[1:]):
    found = search_commons_video(q, era=ERA)
    print(f"\n{q!r}: {len(found)} Commons clip(s)")
    for c in found[:5]:
        print(f"  {c['year']}  {c['duration']:.0f}s  {c['license']}  {c['title']}")
    if found:
        out = f"probe_out/clip{i}.mp4"
        ok = fetch_commons_video(found[0], out)
        print(f"  cut: {ok} ({_clip_seconds(out) if ok else 0:.1f}s)")
    for j, c in enumerate(found[1:3], 1):
        o = f"probe_out/clip{i}_{j}.mp4"
        print(f"  cut {j}: {fetch_commons_video(c, o)}")
    print(f"  newsreels: {len(search_archival_video(q))}")
