# Background music

Drop tracks here (.mp3/.wav) and the pipeline will pick from them per video.
Every track gets ducked under the narration and faded out.

- **Shorts**: one track picked at random per video, looped for its length.
- **Long-form**: one track per CHAPTER, not a random pick — see
  src/longform_video.py's MUSIC_OPENING_TRACKS / MUSIC_FINALE_TRACK. The
  first four chapters play these four Kevin MacLeod tracks, in this exact
  order, with a smooth crossfade at each chapter change:
    1. `Crossing the Threshold - Kevin MacLeod.mp3`
    2. `A Singular Perversion - Kevin MacLeod.mp3`
    3. `Epilog - Kevin MacLeod.mp3`
    4. `Measured Paces - Kevin MacLeod.mp3`
  The LAST chapter always plays `March of the Mind - Kevin MacLeod.mp3`
  (this wins over the "first four" rule on a video with 4 chapters or
  fewer). Any chapter in between draws a random pick from whatever else is
  in this folder — that is the pool for new tracks going forward; drop a
  file in and it is automatically eligible for the middle chapters, no code
  change needed. A named track that is not actually present renders as
  silence for its chapter rather than breaking the video.

Where to get free, monetization-safe tracks:
- YouTube Audio Library (youtube.com/audiolibrary) — filter by "Attribution
  not required". No credit needed anywhere; just drop the file in.
- Creative Commons tracks (e.g. Kevin MacLeod / incompetech.com, CC BY 4.0)
  are also free to monetize, but the license requires the exact credit text
  to appear in the video description — see ATTRIBUTION below. Do not add one
  of these without also adding its sidecar file, or the video ships out of
  compliance with the license silently.
- Recommended vibes for this niche: dark ambient, tense, drone, somber piano,
  melancholy strings, war-documentary — not mystery/suspense/noir, which
  reads as true-crime rather than "how an institution walked into disaster".

Do NOT use random tracks from the internet with no stated license — a single
Content ID claim can redirect all the revenue of a video to the rights holder.

## Attribution

A track needs a description credit only if its own license requires one
(YouTube Audio Library's "Attribution not required" tracks never do). When it
does: save the exact required credit text as a sidecar file, same filename as
the track plus `.attribution.txt` — e.g. `Some Track - Some Artist.mp3` pairs
with `Some Track - Some Artist.mp3.attribution.txt`. The pipeline reads it
automatically (see src/music_attribution.py) and appends it to the
description of whichever video actually used that track. No sidecar file
means no credit is added, so a track that needs one and has no sidecar is a
silent license violation — always add both files together.
