# The Archive Paradox — Shorts handbook

The one page a new session reads to run this channel. Keep it current; it is
cheaper than any chat history. (Written 9 Nov 2026 from the session that built
the format.)

## What the channel is

YouTube channel "The Archive Paradox" (repo BagiTheKing/shadow-archives-bot).
**Shorts only** (owner, 5 Nov): no long videos unless a huge event. Each Short
starts from **this week's biggest world news** and shows the **moment in
history that explains it** — same country, neighbour or same actors, a settled
event before 2010. Never a loose theme.

Results so far (views at ~2-3 days):

| Short | Views | Stayed to watch |
|---|---|---|
| Israel warns Iran / Dimona 1960 | 1,938 | 70% |
| Tankers burn in Hormuz / Tanker War 1987 | 1,100 | 66% |
| Paraguay bank collapse / Argentina 2001 | 140 | 63% |
| French student protests / May 1968 | 224 | 69% |
| Russia–Ukraine talks / Budapest Memorandum 1994 | posted 9 Nov | |

Lesson: retention is fine on all of them; **views follow the size of the news
story**. Pick the week's biggest world story first (Iran, Israel,
Russia–Ukraine, US, China, Korea…), then its history. Niche national stories
die at ~150 views.

## The owner

- Wants **very short messages**. No essays. One-line captions.
- Hates waste: never burn the free Gemini quota on test runs (it once went in
  9 test runs of an unfinished pipeline). Test the piece you changed, locally.
- Hates sloppy pictures (wrong person, wrong era, cropped-off faces) and
  filler narration. Plain, confident, well-written English.
- Voice: Kokoro `am_onyx` (the owner compared 11 voices and kept it).
- Delete political chain-message spam comments; keep real disagreement.

## The format (tools/paradox_short.py)

A folder `owner_media/<story>/` holds:

- `short.json` — the Short. Example: `owner_media/budapest-1994/short.json`.
  - `then_year`, `music` ("Beyond the Lows"), `now_tag` ("NOW" — never label a
    photo with a year it was not taken), `now_focus`/`then_focus` [x, y]
    (fractions of the picture: where the face is)
  - `images`: key → `{url, credit, focus}`; footage: `{video: <Commons
    transcode url>, start: s, duration, x, url, credit}`
  - `lines`: `[{show:"opening", say}, {show:"opening", say}, {show:<image
    key>, tag:"DEC 2001", say, also:<second image key>}, …, {show:"now", tag,
    say}, {show:"end", say:<question>}]`. Line 1 = this week's news; line 2 =
    the turn to the past ("But in 1994…" / "Ukraine has been promised security
    before."); 5-7 picture lines; a now line; a question for the comments.
    ~120-140 words → 45-55 s. `also` cuts to a second picture halfway (a new
    shot every ~2.5 s). The first picture alone is capped at 2.5 s.
  - `end`: `{title: 1-4 word question, sub: "BUDAPEST, 1994", pointer: "TELL
    US IN THE COMMENTS", arrow: "right"}`
- `sources.json` — `now` / `then` pictures `{url, credit}` (downloaded on the
  runner).
- `short_meta.json` — `{title (ends " #shorts", < 95 chars), description
  (news + history + picture credits + hashtags), tags, comment (the
  question)}`.

## The tools (GitHub Actions; this machine cannot reach Commons/Wikipedia)

| Workflow | Inputs | Gives (on branch `preview-media`) |
|---|---|---|
| Media Catalog `media_catalog.yml` | categories, queries (`\|`-separated) | `catalog.json` (V1.. videos, P1.. photos: url, play, year, licence, artist) + numbered contact sheets `photos_N.jpg` (5×8 tiles 360×249) |
| Media Probe `media_probe.yml` | mode=footage, queries=<video url> | `clip_0.jpg`: a frame every 8 s with timestamps |
| Paradox Short `paradox_short.yml` | owner_media=owner_media/<story> | `short.mp4` |
| Upload Short `upload_short.yml` | meta_file=owner_media/<story>/short_meta.json, file=short.mp4 | public Short + first comment (link in the job log) |
| Hold Scheduled Uploads `hold_scheduled.yml` | hold=false | latest 50 uploads with views (stats) |
| Daily Paradox Short `daily_paradox.yml` | — | the Gemini-only builder (tools/daily_paradox.py). Off: it chose weak parallels and wrong pictures |

Look at a contact-sheet tile: tile n of sheet s is at x=(k%5)*360,
y=(k//5)*249 with k=(n-1)%40, s=(n-1)//40+1. Crop the 10-20 candidates into
one image and look at that, not whole sheets (cheaper).

Check a render: `ffmpeg -i short.mp4 -vf "fps=1,scale=180:-1,tile=10x6"
-frames:v 1 frames.jpg` and look: right person in frame (also in the
split-screen top half), no text off screen, picture order matches the words.

Workflows run on `main`: commit owner_media and code to `main` (and to your
session branch), then dispatch.

## Picture rules learned the hard way

- The parallel must share country/actors with the news ("Paraguay bank
  collapse → 2008 US crisis" was wrong; "→ Argentina 2001" right).
- NOW = a recent, serious photo of the person at the centre of the news
  (never the historical event's photo; a 1968 crowd was once labelled NOW).
- Every past picture from the right event and era (a 2004 Sorbonne photo for
  1968, a 1919 Seattle strike for France, Japan's PM on a Bush line — all
  caught). No drills, re-enactments, museum displays.
- Set `focus` so the named person is in the crop (Nixon filled a de Gaulle
  line until focus moved to 0.86).
- Footage beats stills when it is the real moment (De la Rúa's state-of-siege
  address; Clinton in Kyiv, 12 Jan 1994).

## Option 3 — the plan being built (owner, 9 Nov)

Goal: fully automated daily Shorts at the same quality, cheap.

1. **History library** (built by Claude, weekly): `library/<slug>/` = the
   history half of a Short, done once: `entry.json` with `actors` (countries,
   people, places that appear in headlines: "Iran", "Hormuz", "Strait of
   Hormuz", "IRGC"), `then_year`, the 5-7 picture lines (facts checked), the
   pictures/footage chosen by eye, `then` picture, end question ideas, `now_people`
   (Wikipedia article titles of leaders whose photo can be NOW). ~40 entries
   across the actors that dominate the news. Existing owner_media folders are
   the first entries.
2. **Daily job** (GitHub, Gemini, free): read today's headlines
   (src/trend_source.py), take the biggest story, match it to a library entry
   by actors (code first, Gemini to choose between matches). Gemini writes only
   the today half: line 1 (the news, only what the headline says), line 2 (the
   turn), the now line, the end question, title, description — checked by code
   (headline words, length, no invented numbers). NOW picture: the lead photo
   of the `now_people` article, checked recent. Render, frame checks, **post
   automatically**. No match → post nothing and log why (the weekly session
   adds an entry). The news must be today's: the history is stable, the today
   half is always written fresh.
3. **Weekly Claude session** (cheap model, fresh context): review the week's
   Shorts and stats, fix problems, add library entries for the stories that
   are big right now and likely next week.

## Running this from a session

- Start cheap: read this file, not old chats. Use Monitor/background waits for
  workflow runs instead of polling in the conversation.
- When your context gets long, write what you learned here (and current state
  below), start a fresh session with create_session (cheapest capable model,
  same repo) and a prompt pointing to this handbook, move the scheduled tasks
  to it (create new ones bound to it, delete yours), tell the owner the new
  session's name in one line, then stop.

## Current state

- 9 Nov 2026: Budapest 1994 Short posted (https://youtube.com/shorts/oclXehb1ujM).
- Daily hand-made Shorts were run by a scheduled task in the original session
  (trigger "Daily Short (hand-made)"); it is being retired as this handbook's
  session takes over.
- Long-form schedules (weekly_timely, weekly_longform, daily_publish,
  topic_scout) are commented out. Keep them off.
