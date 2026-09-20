"""Attribution lookup for background music tracks that require it.

Most tracks in music/ are YouTube Audio Library ("Attribution not required")
and need nothing in the description. A Creative Commons track (e.g. Kevin
MacLeod / incompetech.com, CC BY 4.0) is different: the license is free to
use commercially, but only if the exact required credit text appears in the
video description. Missing that credit is a real license violation, not a
style choice — so this is opt-in-by-sidecar-file rather than a lookup table
that could quietly fall out of sync with what's actually in the folder.

Convention: a track that needs credit gets a sidecar text file next to it,
same filename plus '.attribution.txt', containing the exact credit block the
license requires. A track with no sidecar needs no attribution. See
music/README.md for how to add one.
"""

import os


def attribution_for(track_path: str) -> str:
    """The exact credit text to put in the video description, or "" if this
    track needs none."""
    if not track_path:
        return ""
    sidecar = track_path + ".attribution.txt"
    if os.path.exists(sidecar):
        with open(sidecar, encoding="utf-8") as f:
            return f.read().strip()
    return ""
