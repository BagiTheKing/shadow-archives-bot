# Shorts spec (the daily lane)

The rules for every daily Short, the counterpart of `VIDEO_SPEC.md` for the
long-form videos. Every change to the Shorts pipeline is checked against this
whole list before it is pushed. Add to it when a new rule is agreed; never
drop a rule without the owner's say-so.

Each rule says where it is enforced. **Code** means a script, frame or render
that breaks it is rejected or corrected automatically, and a rejected script
goes back to the writer with the reason. **Prompt** means the writer is asked
and nothing checks it yet: those are the rules to move into code next.

## Topic
1. **One story per day, never twice.** A pool topic already on the channel is
   never picked, and a run stands down if a Short of this lane went out in the
   last 20 hours (`DUPLICATE_GUARD_HOURS`). Shorts cut from the long-form
   videos (`is_clip`) do not count. Code: `main.plan_run`,
   `published_history`.
2. **Reach first.** Only topics rated WIDE are picked automatically
   (`REACH_FLOOR`), and a topic the Shorts feed is already full of is
   skipped. Code: `topics.pick_topic`, `topic_reach.vet`.
3. **The promise is kept.** The last Short's "Next file" is today's story,
   unless it has since been published. Code: `promised_topic`.

## Script
4. **The hook.** Scene 1 is at most 12 words (14 accepted), never opens
   with setup ("In 1628", "Imagine", "Have you ever"...), and carries a
   number, a name or a superlative. Code: `_validate_hook`.
5. **The turn.** Once, in scene 3 or 4, one short line spoken to the viewer
   just before things go wrong ("Now watch what the captain does next."),
   pointing at what really happens next. Never in the hook. Code
   (`spec_issues`): the hook may not speak to the viewer, a middle scene
   must, and no more than two lines may. Prompt: at most 8 words, not a
   fake cliffhanger.
6. **Length and clarity.** 4-6 scenes (5 asked), 90-160 words (110-130
   asked), no sentence over 25 words (15 asked). Code: `_validate`. Prompt:
   one storyline, one mechanism a viewer could repeat, at most two named
   people, every term explained in the same sentence.
7. **The ending.** Scene 5 gives the cost, then one line that lingers, never
   a moral. Its punch phrase (2-6 words) is words the last line really
   speaks. Code: `normalize_punch_phrase` (the phrase is dropped or replaced
   by the last sentence when it is not in the narration). Prompt: no moral.

## Facts
8. **From the source, or not at all.** The script is written from the
   story's Wikipedia fact sheet (`SHORTS_FACTS=prefer`). Every figure of 13
   or more in the narration, title and thumbnail, every name, and every quote
   must be in the article: figures as written or rounded within 10%, quotes
   word for word. Without a fact sheet, no quotes at all. Code:
   `shorts_facts.unsupported_claims`, `spec_issues`. The source is linked in
   the description.
8a. **One real quote**, from a person in the story, attributed, when the
    sheet has a short one. Prompt; its accuracy is code (rule 8).

## Title, thumbnail, comment
9. The title fits the space left by the series marker and `#shorts`, and its
   grammatical shape may not be most of the last 8 titles. Code:
   `validate_title`. Prompt: the consequence, not the event; a role, not a
   name nobody knows; one searchable proper noun.
10. Thumbnail text 3-4 words (a sentence is replaced from the title), badge
    the year. Code: `normalize_thumbnail_fields`.
11. The comment question is posted as the first comment and never spoken
    (`SPEAK_QUESTION=0`). Code.

## On screen
12. **A new picture for every sentence, each used once.** Each scene's shots
    are one per sentence, each showing what that sentence says; the cut
    lands where the sentence starts in the narration, and each picture gets
    one slow move, taking turns across the video. No picture twice, no
    punch-ins on the same picture (run #123 showed they read as the video
    lagging). At most 10 pictures (`SHORTS_MAX_IMAGES`), none on screen for
    under 1.8 seconds: over budget, the shortest neighbouring shots merge.
    Code: `shorts_shots`, `video_engine.plan_shots`; the writer ignoring the
    shot list is rejected (`spec_issues`). Prompt: the picture shows what the
    sentence says, scene 1 shows the subject itself.
12a. **What the video is about, on screen from the first frame**: the
    subject and year ("THE CARRINGTON EVENT · 1859") over the hook. Code:
    `topic_label`, `generate_subtitles`.
13. **Paintings in one style, dated.** Every generated frame gets the
    channel's style and the story's era stamped on at generation, whatever
    the writer asked for. A video with under 60% of its frames from Workers
    AI (when configured) is not published. Code: `build_generation_prompt`,
    `check_visual_quality`. Prompt: one figure at most, never hands.
14. **Real pictures first, matched and credited.** Each shot with an archive
    search is looked up on Commons and the Library of Congress before any
    painting is generated; a match (the file title carries the search words)
    is that shot's picture. Up to 5 a Short (`SHORTS_ARCHIVE_FRAMES`), or the
    article's free lead image when nothing matched; every one credited in the
    description. Code: `shorts_archive`.
15. **Captions.** At most 4 words at a time, broken on punctuation, every
    figure in the accent yellow. Code: `generate_subtitles`.
16. **The punch phrase** slams on screen in the upper third as it is spoken,
    and the bottom captions stand down for those words. Code.
17. **The ending card.** When a next story is lined up (`SPEAK_TEASE=1`),
    the last scene is the next-file card: that story, spoken as "Next file:
    ...", on a still card, with the subscribe ask drawn into it. Code:
    `add_outro_scene`, `draw_outro_card`.

## Sound
18. Music at 10% under the narration, drawn to a quarter of that for the
    story's last line and back up for the next-file card. A low impact under
    the hook's first figure and under the punch phrase, nothing else. Code:
    `music_volume`, `sound_cues`.

## Process
19. Check a change against this list before pushing. Test writing with
    unit checks on real scripts, visuals with a short sample render, then one
    manual dry run of the workflow ("Render only, do not upload"), and watch
    it before it goes to the owner.
