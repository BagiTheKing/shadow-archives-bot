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

## Repeats (owner, 9 Nov)

Never repost a Short. The Paraguay/Argentina Short (140 views, 63% stayed,
99% Shorts feed, 0 subscribers) shows YouTube holds back a Short people like
if it is not fresh. A library entry is reused only after 90 days
(`COOLDOWN_DAYS`) and with a different today half. At 5 Shorts a week that
needs ~65 entries to run daily without gaps: the library must keep growing.

## YouTube auto-dub (owner, 10 Nov)

A Short can play in another language for the owner: YouTube auto-dubs for
viewers whose language differs. The file itself is English (Kokoro am_onyx,
checked by tools/check_short.py's language gate in the daily job). Not a bug:
do not re-render or delete because of it.

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

## Option 3 — built 9 Nov 2026

Fully automatic daily Shorts, cheap: Claude builds the history once, the free
Gemini writes only today's half.

**Library** `library/<slug>/entry.json` (first 5 = converted owner_media):
`event`, `then_year`, `actors` (words that appear in headlines; whole-word,
case-insensitive; keep them specific, a generic word matches every story),
`then` (opening picture), `now_people` (Wikipedia article titles whose lead
photo can be NOW, with `focus`), `images` + `lines` (the picture lines, same
format as short.json: show/tag/say/also; footage allowed), `facts` (one
sentence summary the today half may draw numbers from), `examples` (a good
line1/line2/now/question, for style), `end_ideas` (`{title, sub}`),
`then_focus`, `music`. `python -m unittest tests.test_library_short` validates
every entry (images referenced exist, credits present, >= 4 picture lines).
New entries: pictures by eye from Media Catalog sheets, facts checked, as for
a hand-made Short. History lines say nothing about this week.

**Daily job** `daily_library.yml` (08:47 UTC Mon-Fri; inputs post=true|false):
`tools/daily_library.py` takes headlines (`daily_paradox.headlines()`), code
matches actors to entries (`src/library_short.py`, entries posted in the last
90 days are skipped), Gemini writes line1/line2/now/question/title/etc. (it
may answer fit=false) and code checks it (`today_problems`: starts "This
week,"/"Today,", names and numbers must be in the headline, word counts, title
format), NOW = lead photo of the named person, must be recent and of that
person. Then Paradox Short render, `tools/check_short.py` (size, 35-65 s,
sound, black frames), `upload_short.py` (public), `library/log.json` result
(posted / skipped + reason / not_posted) committed to main. No match = no
post. Cost: usually 1 Gemini call a day.

**Weekly review** (scheduled task "Weekly library review", Sundays 14:07 UTC,
fresh session): read `library/log.json`, the week's stats (Hold Scheduled
Uploads) and the Shorts themselves (frames); fix bad entries/code; add 3-5
entries for actors big in the news now (WebSearch for what is big; Media
Catalog for pictures); run the tests; commit to main; update Current state.
Library target ~40 entries.

## Running this from a session

- Start cheap: read this file, not old chats. Use Monitor/background waits for
  workflow runs instead of polling in the conversation.
- When your context gets long, write what you learned here (and current state
  below), start a fresh session with create_session (cheapest capable model,
  same repo) and a prompt pointing to this handbook, move the scheduled tasks
  to it (create new ones bound to it, delete yours), tell the owner the new
  session's name in one line, then stop.

## Current state

- 10 Nov 2026: library has 9 entries (5 converted + cuban-missile-crisis-1962,
  iran-hostage-crisis-1979, panama-invasion-1989, nixon-in-china-1972; all four
  new ones previewed by render, pictures OK). The 08:47 schedule did NOT fire on
  its first day (GitHub often skips a new workflow's first cron): the hand-made
  task posted https://youtube.com/shorts/OVEbZe9CY6Q (Trump/Xi/Putin, Nixon
  1972). If the schedule misses again, dispatch daily_library.yml by hand.
  Tools: tools/library_add.py (catalog -> entry), tools/library_preview.py
  (render an entry). Renderer drops pictures over 40 MB: no TIFFs.

- 9 Nov 2026 (evening): Option 3 built and tested end to end (run 1, post=false:
  Paraguay/Argentina Short rendered fine, frames OK). Daily Library Short is
  scheduled (08:47 UTC Mon-Fri) and posts by itself. The 5 library entries are
  all on the 90-day cooldown (hand-posted 5-9 Nov, seeded in library/log.json),
  so it will skip until new entries exist: NEXT = add ~5 entries for actors big
  now (Iran/Hormuz variants, US, China, Korea, Gaza...). The hand-made 09:41
  task is the backup; switch it off after the daily job posts 2-3 good Shorts.
- Channel (9 Nov): 56 subscribers, 90-day Shorts views 18.6K: monetisation
  (1,000 subs + 3M/10M Shorts views) is far off; do not promise dates.

- 9 Nov 2026: Budapest 1994 Short posted (https://youtube.com/shorts/oclXehb1ujM).
- Daily hand-made Shorts were run by a scheduled task in the original session
  (trigger "Daily Short (hand-made)"); it is being retired as this handbook's
  session takes over.
- Long-form schedules (weekly_timely, weekly_longform, daily_publish,
  topic_scout) are commented out. Keep them off.
