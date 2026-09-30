# Long-form video spec (story mode)

The rules the owner has agreed for every long-form video, on any topic.
Every change to the long-form pipeline is checked against this whole list
before it is pushed. Add to it when a new rule is agreed; never drop a rule
without the owner's say-so.

## Structure
0. **Story mode is the only long-form format**, on every lane (named
   topics, trends, encore). A run whose story cannot be built stops; the old
   six-chapter arc is never used.
   A run that loses a chapter, or gets one back at under half its length,
   stops instead of uploading half a story.
1. **Chapter 1 is a teaser.** The big picture only: what happened, who
   stood against whom (sides and countries, not individuals), and why it still
   matters. No specific days, dates or clock times, no individual introduced.
   It ends on the question the video answers. On screen: no cast cards, no
   date ruler, no maps.
2. **Chapter 2 introduces the characters as a story.** A well-written
   passage that shows who acts with whom and against whom, never a list.
   Every cast member (at most 5) is introduced here, each in a short scene of
   their own (about 5-8 seconds), and gets their cast card on that scene.
   No cast cards anywhere else.
3. **Going back in time (option A).** A chapter may start earlier than the
   previous one ended (another front, another side). When it does, it opens
   with one or two sentences spoken to the viewer that say so ("While all
   eyes were on the desert, a second war was starting in Jerusalem. Let's go
   back to that same Monday morning."). Checked by code.
4. **The narrator may speak to the viewer** at turning points ("Remember
   Nasser's gamble?", "Now watch what happens next"), sparingly, not in every
   sentence.
4a. **Signposts.** Where the main event (war, disaster, crisis) begins and
   where it ends, the narrator says so in one short line ("And this is
   where the war began."). Checked by code.
5. Simplicity: at most 5 named people (each introduced with their role), at
   most 5 named places, no unit names, few rounded figures, short sentences
   (see story_mode.py). Checked per chapter.
6. Facts, names, dates, figures and quotes only from the verified fact sheet.
6a. **The ending reaches forward.** The last chapter ends on what the event
   led to in the years after (one or two things, each with its year), from
   the article's own aftermath/legacy, never later than 2010 and never
   "today" or "nowadays". Checked by code.

## On screen
7. **Cast cards** keep the approved look (freeze, duotone, cut-out, outline,
   speed lines, name slam, role stamp; DECISION MAKER only for the one who
   decided). The card takes over its whole scene from the first frame: the
   portrait or footage is on screen, then it freezes on the spoken name.
   Never spliced into the middle of a scene. Footage when a clip passes the
   face match, eyes-open and clean cut-out checks; photo otherwise.
8. **Date ruler** at the bottom: rises before a spoken date, rolls from the
   last date to the new one, lands as the date is spoken. Not in chapter 1.
9. **No top-left date/place stamps.** The ruler shows time, maps show place.
10. Names and dates land on the exact spoken word: timings for those words
    are measured, not estimated.
11. Quote cards: real quotes only, a named person as speaker, whole
    sentences, no ellipsis; two different people, cast members first, the
    second card from the other side of the story when one has a quote.
    Only people in the story speak: a cast member or someone the fact
    sheet names. When no real quote is left for a card, it gets an original
    line written for that moment, never attributed to anyone (owner,
    2026-09-29: better than no card).
    Every card ends on a red phrase (the person's own words).
12. **Maps** for each front or turning point the story moves to (up to 4,
    one per chapter, each a different place), never in chapters 1-2 or the
    verdict; a map only where the narration speaks its place.
13. Footage must match its scene (title and categories), never still
    photographs; war newsreels only for combat scenes, never diplomacy reels.

12a. **Claim sources.** A line that clearly matches a cited sentence of the
    Wikipedia article shows that footnote's source at the bottom right
    ("SOURCE" in yellow, then the work in grey, look B), not over maps or
    cast cards. No model decides this, and no match means no tag, never a
    guessed source. The description lists the article and every work shown.

15. **Trend topics come from the watched channel first**: its theme is
    anchored to a whole event with sides and a turning point (a war, crisis
    or disaster that ended by 2010); news headlines are only the fallback,
    and the log says so when it is used.

16. **Three thumbnails per video** for YouTube's Test & Compare (added by
    the owner in Studio when the video goes public): face-off (two leaders
    from opposite sides), one person (the one who decided), map (the video's
    own map places). The first one made is uploaded; all are kept as the
    run's thumbnail artifact for 90 days. Titles: under 60 characters, the
    searchable name plus a stake, saying something different from the
    thumbnail; neutral about present-day conflicts.

## Process
14. Check a change against this list before pushing. Test writing with a
    script-only run, visuals with short sample renders, then one full run,
    and watch the result before sending it to the owner.
