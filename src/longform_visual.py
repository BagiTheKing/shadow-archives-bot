"""
Long-form visual engine — 16:9, archival-first.

The old version of this file produced the "bad PowerPoint" problem: one AI
image per ~45 words meant a frame could sit on screen for 30+ seconds, chapter
openers were flat locally-drawn title cards, and every single visual was an AI
painting. Three changes fix that:

1. REAL IMAGERY FIRST. Each scene carries an archive_query naming its real,
   photographable subject. Wikimedia Commons (and a couple of other archives,
   see archive_engine.py's search_archives) is searched before anything is
   generated; a genuine photograph of the ship, the treaty or the man beats an
   AI guess every time one exists. When no still exists, real archival VIDEO
   footage is tried next — but only from collections whose public-domain
   status is a documented fact, not a per-upload claim (see
   TRUSTED_VIDEO_COLLECTIONS), capped at a few clips a video and always
   trimmed to a short, fixed length regardless of the source item's own
   length. Optional muted Pexels b-roll fills whatever is still missing when
   a PEXELS_API_KEY is configured. Pollinations remains the fallback, not the
   default. A recurring subject (the story's central figure) is capped at
   MAX_REAL_PHOTOS_PER_SUBJECT real appearances, steered toward a different
   real moment on each repeat — a real Trending run showed the same "official
   1993 portrait" style four times for one person across the video, which
   read as the picture looping even though the files were technically
   distinct; past the cap, later scenes fall through to generation instead.

2. NO SHOT OUTLIVES ITS WELCOME. A scene longer than MAX_SHOT_SECONDS gets
   extra images (a second Commons result, or a re-seeded generation), and the
   video engine cuts between them inside the scene. The cutting rhythm no
   longer depends on how the LLM happened to segment the narration.

3. CHAPTER CARDS ARE GONE. Chapter-first scenes get real imagery like any
   other scene; the chapter title is typeset over the footage by the video
   engine, the way an actual documentary does it, instead of pausing the film
   for a slide.

Frames are still saved pre-upscaled at KB_W x KB_H so the renderer needs no
per-frame scale filter (see longform_video.py for why that matters).
"""

import math
import os
import re
import time

from PIL import Image

from archive_engine import (search_archives, search_commons,
                            download_archival_image, format_credit,
                            pexels_available, fetch_broll_video,
                            extract_poster_frame, search_archival_video,
                            fetch_archival_video, format_archival_video_credit,
                            search_commons_video, fetch_commons_video,
                            footage_matches, article_lead_image, article_images,
                            category_images, commons_category_of)
from visual_engine import download_image, make_fallback_image

# Delivery resolution for the long-form video.
LF_W, LF_H = 1920, 1080

# 1.5x delivery: enough headroom for a 1.2x Ken Burns zoom to stay above 1080p,
# without the 4x-area cost of the Shorts path.
KB_W, KB_H = int(LF_W * 1.5), int(LF_H * 1.5)

THROTTLE_SECONDS = 6            # Pollinations anonymous rate limit

# Longest a single still may hold the screen. Thirteen seconds is already slow
# by documentary standards; past it the video reads as a slideshow.
MAX_SHOT_SECONDS = 13.0
MAX_SHOTS_PER_SCENE = 3

# B-roll is connective tissue, not the main dish: too many stock clips and the
# video stops looking like it is about anything in particular.
# Stock footage is real but not OF the story: a Houthi teaser drew a woman
# in pearls for "two nations bound their fates together", fog and dunes
# (20 Oct). A viewer reads an unrelated real clip as a mistake, worse than a
# matched painting, so it is off unless MAX_BROLL_CLIPS says otherwise.
MAX_BROLL_CLIPS = int(os.getenv("MAX_BROLL_CLIPS", "0"))
MIN_BROLL_SCENE_SECONDS = 6.0

# Minimum scenes between two b-roll clips. The first live run granted b-roll
# first-come-first-served, so every clip landed in the opening minutes and the
# back half was wall-to-wall stills. Spacing them out is what "mixed" means.
BROLL_MIN_GAP = 4

# Real archival footage (see archive_engine.py's TRUSTED_VIDEO_COLLECTIONS) is
# a garnish on top of archival stills, not a replacement for them — the owner
# was explicit that photos with Ken Burns motion already carry the video, and
# two or three real clips are enough. Spaced wider than b-roll on purpose, so
# the few clips that do appear land in different chapters instead of
# clustering, which is what "diversity" actually asks for here.
#
# Raised once Commons footage was added: with stills tried first, footage was
# only ever looked for when no photo existed, which is almost never, so not
# one clip reached a video. Real moving pictures of the actual events (the
# war, a leader speaking) now get first refusal on a spaced set of scenes.
#
# Owner, 2026-10-20: real footage first on EVERY scene, then real photos,
# and a generated image only when reality has nothing that matches (many
# viewers dislike AI pictures). No cap and no spacing on real
# footage any more, and a scene needs only ARCHIVAL_VIDEO_MIN_SCENE seconds.
MAX_ARCHIVAL_VIDEO_CLIPS = int(os.getenv("MAX_ARCHIVAL_VIDEO_CLIPS", "999"))
ARCHIVAL_VIDEO_MIN_GAP = int(os.getenv("ARCHIVAL_VIDEO_MIN_GAP", "0"))
ARCHIVAL_VIDEO_MIN_SCENE = float(os.getenv("ARCHIVAL_VIDEO_MIN_SCENE", "2.5"))

# Photography exists from roughly here; before it, a Commons "photo" of the
# subject is by definition a modern anachronism.
PHOTOGRAPHY_YEAR = 1840

# Titles containing these read as period-appropriate artwork even when the
# file also carries a modern year (a 1907 painting OF 1415 is fine content).
ART_WORDS = ("painting", "engraving", "portrait", "tapestry", "manuscript",
             "miniature", "fresco", "woodcut", "etching", "drawing",
             "illustration", "map", "chart", "azulejo", "mosaic", "relief")

# Modern-photo tells in a Commons title. Only consulted for pre-photography
# stories, where a match means the search drifted to the present day —
# the "2020s aerial of Ceuta over 1415 narration" failure.
_MODERN_TITLE_RE = re.compile(r"\b(19|20)\d{2}\b")
_MODERN_TITLE_WORDS = ("aerial", "panorama", "skyline", "beach", "street",
                       "marina", "airport", "stadium", "modern", "night view")

# Archives are captioned with subjects, not actions: a Commons/LoC file about
# Kim Jong Il is titled with his name, never with "Kim Jong Il signing the
# withdrawal decree" verbatim. A specific, action-bearing archive_query for a
# segment about a person's own decision (the writer's prompt allows this even
# though it favours bare-subject shapes) can return nothing while the exact
# same story's other, plainer "<person> <year>" queries return real photos
# several times over — this widens the failed query back down to its bare
# subject (a run of Title Case words, closest to a proper noun) plus any year
# mentioned, before giving up and routing the scene to AI generation.
_YEAR_RE = re.compile(r"\b(1[5-9]\d{2}|20\d{2})\b")
_PROPER_NOUN_RUN_RE = re.compile(r"\b[A-Z][a-zA-Z.'-]+(?:\s+[A-Z][a-zA-Z.'-]+){0,4}\b")


def _widen_query(query: str):
    """The bare-subject fallback for a specific archive_query that found
    nothing, or None when the query has no proper-noun core to fall back to
    (or widening would just repeat the same search)."""
    names = _PROPER_NOUN_RUN_RE.findall(query)
    if not names:
        return None
    # A person's name is never shortened: "Ali Salem al-Beidh" widened to
    # "Ali Salem" and drew a US Air Force officer's portrait for Yemen's
    # southern leader (20 Oct). A query that is nothing but a name (and a
    # year) either finds that person or nobody.
    words = [w for w in re.sub(r"\b\d{4}\b", "", query).split() if w]
    if words and all(w[:1].isupper() or re.match(r"(al|el|bin|ibn|bint|abu|de|"
                                                  r"van|von|da|di|le)\b", w, re.I)
                     for w in words):
        return None
    core = max(names, key=len)
    year = _YEAR_RE.search(query)
    widened = f"{core} {year.group(0)}" if year else core
    return widened if widened.lower() != query.strip().lower() else None


# A recurring central figure legitimately needs to appear more than once —
# but the real Kim Jong Il test video asked for the literal, unchanged query
# "Kim Jong Il 1993" at four widely-spaced scenes, and Commons/LoC search
# keeps returning candidates from the same narrow "official 1993 portrait"
# style for an identical query, so the video read as looping the same photo
# even though used_urls (below) guaranteed four technically distinct files.
# used_urls stops the exact same FILE repeating; nothing stopped the same
# SUBJECT reappearing from the same stylistic pool, which is what a viewer
# actually notices.
MAX_REAL_PHOTOS_PER_SUBJECT = 3

# On a repeat appearance, tried before the bare query — steering the search
# toward a different real moment of the same subject (a meeting, a ceremony)
# instead of the closest match to whatever the first appearance's exact
# phrasing already pulled. Rotated by how many times this subject has
# already appeared, so a fifth+ appearance (already capped by
# MAX_REAL_PHOTOS_PER_SUBJECT and routed to generation) never needs one.
_SUBJECT_QUALIFIERS = ["meeting", "ceremony", "military", "delegation", "podium"]


def _subject_key(query: str) -> str:
    """Normalises an archive_query to the recurring subject it names, so
    "Kim Jong Il 1993" and a later scene's identical or near-identical query
    for the same person are recognised as the same subject regardless of
    which year happens to be attached."""
    return _YEAR_RE.sub("", query).strip().lower()


# How much later than the story's year a photo may be taken: a story's
# legacy chapter reaches a decade or two on (Gaza 1987 to the 2007 takeover),
# never past the history cutoff.
PHOTO_YEARS_AFTER = 20
HISTORY_CUTOFF = int(os.getenv("HISTORY_CUTOFF_YEAR", "2010"))


def _era_appropriate(candidate: dict, era_year) -> bool:
    """Whether a Commons candidate can appear in a story set in era_year."""
    year = candidate.get("year")
    if era_year and year and (year > era_year + PHOTO_YEARS_AFTER
                              or year > HISTORY_CUTOFF):
        # a 2020s London protest photo stood in for Gaza 1987 (10 Oct) and
        # became the video's thumbnail under "4 WORKERS KILLED 1987"
        return False
    if not era_year or era_year >= PHOTOGRAPHY_YEAR:
        return True
    low = candidate.get("title", "").lower()
    if any(a in low for a in ART_WORDS):
        return True
    if _MODERN_TITLE_RE.search(low):
        return False
    return not any(w in low for w in _MODERN_TITLE_WORDS)


def _shots_for(duration: float) -> int:
    """How many distinct images a scene of this length needs."""
    if not duration or duration <= 0:
        return 1
    return min(MAX_SHOTS_PER_SCENE, max(1, math.ceil(duration / MAX_SHOT_SECONDS)))


def _to_kb_size(path: str):
    """Upscale a downloaded frame to the Ken Burns working size, once."""
    img = Image.open(path).convert("RGB")
    if (img.width, img.height) != (KB_W, KB_H):
        img = img.resize((KB_W, KB_H), Image.LANCZOS)
    img.save(path, "JPEG", quality=92)


def _reuse_real_frame(pool, output_path: str, variant: int) -> bool:
    """Re-frames an already-downloaded archival image as a stand-in for a
    failed generation. Punched in and offset so it reads as a different shot
    of the same material rather than a repeat.

    Not mirrored on purpose: flipping an archival photograph reverses insignia,
    signage and text, which on a history channel is worse than reusing it.
    """
    if not pool:
        return False
    source = pool[variant % len(pool)]
    try:
        img = Image.open(source).convert("RGB")
    except Exception:
        return False

    # 78% centre-ish crop, nudged by the variant so successive reuses of the
    # same source frame different parts of it.
    scale = 0.78
    cw, ch = int(img.width * scale), int(img.height * scale)
    max_dx, max_dy = img.width - cw, img.height - ch
    dx = int(max_dx * (0.25 + 0.5 * ((variant % 3) / 2.0)))
    dy = int(max_dy * (0.25 + 0.5 * ((variant % 2) / 1.0)))
    img = img.crop((dx, dy, dx + cw, dy + ch)).resize((KB_W, KB_H), Image.LANCZOS)
    img.save(output_path, "JPEG", quality=92)
    return True


def _vary_prompt(prompt: str, shot: int) -> str:
    """A second AI shot of the same segment should not be the same picture.
    Re-angling the prompt (plus Pollinations' per-call random seed) gives the
    editor a genuinely different frame to cut to."""
    angles = ["", "extreme close-up detail of the scene, ",
              "wide establishing shot of the scene, ", "low-angle view, "]
    return angles[shot % len(angles)] + prompt


def _archival_footage(query, era, idx, output_dir, used_urls):
    """(video_path, poster, credit, label) for real footage of a query, or
    None. Commons with the query as written, then widened; then the
    public-domain newsreels."""
    video_path = os.path.join(output_dir, f"lf_archival_video_{idx:03d}.mp4")
    poster = os.path.join(output_dir, f"lf_scene_{idx:03d}_0.jpg")
    queries = [query]
    widened = _widen_query(query)
    if widened and widened != query:
        queries.append(widened)
    for q in queries:
        for cand in search_commons_video(q, era=era)[:6]:
            if cand["url"] in used_urls:
                continue
            # title and categories only: a long file description mentions
            # everything (an ASEAN newsreel passed for "United Nations 1967")
            if not footage_matches(query, cand.get("tagline", "")):
                continue
            if not fetch_commons_video(cand, video_path):
                continue
            if extract_poster_frame(video_path, poster, KB_W, KB_H):
                used_urls.add(cand["url"])
                return video_path, poster, format_credit(cand), cand["title"]
            os.remove(video_path)
    # DVIDS (media_sources.py): US military footage, only of the story's own
    # years (its 2025 Red Sea strikes are no picture of 1994)
    from media_sources import search_dvids
    # DVIDS holds footage from the 2000s on: older stories skip it rather
    # than pay five calls a scene for nothing
    for cand in (search_dvids(query, "video", 4) if not era or era >= 2000 else []):
        if cand["url"] in used_urls or (era and cand.get("year")
                                        and abs(cand["year"] - era) > 2):
            continue
        if not fetch_commons_video(cand, video_path):
            continue
        if extract_poster_frame(video_path, poster, KB_W, KB_H):
            used_urls.add(cand["url"])
            return video_path, poster, format_credit(cand), cand["title"]
        os.remove(video_path)
    for cand in search_archival_video(query):
        if cand["identifier"] in used_urls:
            continue
        if not fetch_archival_video(cand["identifier"], video_path):
            continue
        if extract_poster_frame(video_path, poster, KB_W, KB_H):
            used_urls.add(cand["identifier"])
            return (video_path, poster, format_archival_video_credit(
                cand["identifier"], cand["title"]), cand["identifier"])
        os.remove(video_path)
    return None


# Scenes about the fighting itself: when their own footage search finds
# nothing, the story's own war newsreels stand in (see _WarPool).
WAR_WORDS = {"tank", "tanks", "strike", "strikes", "jet", "jets", "aircraft",
             "airstrike", "bomb", "bombing", "bombers", "soldiers", "troops",
             "army", "artillery", "battle", "attack", "fighter", "fighters",
             "airfield", "airfields", "paratroopers", "front", "convoy",
             "military", "shelling", "offensive", "infantry", "warship",
             "destroyed", "fighting", "trenches", "trench", "assault", "war",
             "air", "force", "combat", "guns", "gunfire", "frontline"}
MAX_POOL_CLIPS = 6
POOL_SEGMENT_SECONDS = 9


# A newsreel joins the pool only if it is about the fighting: its title and
# description name combat, and its title names no diplomacy. The fifth
# Six-Day War run filled five battle scenes with the UN Security Council
# from the "1967-06-13 Cease-Fire" reel, and opened on a slideshow of the
# general's announcement.
POOL_WAR_WORDS = ("battle", "war ", "fighting", "attack", "offensive", "troops",
                  "tanks", "front", "invasion", "erupts", "air strike", "airstrike",
                  "bombing", "combat", "army", "soldiers")
POOL_PEACE_WORDS = ("cease-fire", "ceasefire", "cease fire", "accepts", " un ",
                    "united nations", "security council", "talks", "peace",
                    "declaration", "voice", "recording", "announcement", "speech",
                    "interview", "press conference", "resolution", "aid", "hulpacties")


def _pool_ok(c):
    title = f" {c.get('title', '').lower()} "
    about = f" {c.get('about', '').lower()} "
    return (any(w in title + about for w in POOL_WAR_WORDS)
            and not any(w in title for w in POOL_PEACE_WORDS))


class _WarPool:
    """The story's own newsreels, cut into fresh segments for battle
    scenes. Only clips dated to the story's year (or the year after), and
    only the first third of a long reel — a newsreel leads with its titled
    story and moves on to others (the 6 June 1967 "Mid-East" reel turns to
    Vietnam and American riots after about 100 seconds)."""

    def __init__(self, topic, era):
        self.topic, self.era = topic, era
        self.clips, self.cursor, self.used = None, {}, 0

    def _load(self):
        self.clips = []
        if not (self.topic and self.era):
            return
        seen = set()
        for q in (self.topic, f"{self.topic} newsreel"):
            for c in search_commons_video(q, era=self.era, limit=15):
                if c["url"] in seen or c.get("year") not in (self.era, self.era + 1):
                    continue
                if (c.get("duration") or 0) < 20 or not _pool_ok(c):
                    continue
                seen.add(c["url"])
                self.clips.append(c)
        print(f"🎞️  War footage pool: {len(self.clips)} newsreel(s) for {self.topic!r}.")

    def segment(self, idx, output_dir, used_urls):
        if self.used >= MAX_POOL_CLIPS:
            return None
        if self.clips is None:
            self._load()
        video_path = os.path.join(output_dir, f"lf_archival_video_{idx:03d}.mp4")
        poster = os.path.join(output_dir, f"lf_scene_{idx:03d}_0.jpg")
        for c in self.clips:
            if c.get("_bad"):
                continue
            dur = c["duration"]
            lo, hi = (8.0, dur * 0.35) if dur > 150 else (1.5, dur - 2.0)
            pos = self.cursor.get(c["url"], lo)
            while pos + POOL_SEGMENT_SECONDS <= hi:
                self.cursor[c["url"]] = pos + POOL_SEGMENT_SECONDS
                got = fetch_commons_video(c, video_path, seconds=POOL_SEGMENT_SECONDS, start=pos)
                if not got and c.get("_static"):
                    c["_bad"] = True       # a slideshow: drop the whole clip
                    break
                if got and extract_poster_frame(video_path, poster, KB_W, KB_H):
                    self.used += 1
                    return (video_path, poster,
                            format_credit(c) + ", newsreel excerpt", f"{c['title']} @{pos:.0f}s")
                pos += POOL_SEGMENT_SECONDS
        return None


def _is_war_scene(scene):
    words = set(re.findall(r"[a-z]+", (scene.get("archive_query") or "").lower()))
    return bool(words & WAR_WORDS)


_POOL_STOP = {"the", "and", "with", "from", "that", "this", "were", "was", "their",
              "they", "into", "over", "after", "before", "about", "which", "while",
              "would", "could", "under", "years", "year", "photo", "file", "jpg"}


def _words(text):
    return {w for w in re.findall(r"[a-z]{4,}", (text or "").lower()) if w not in _POOL_STOP}


def _story_pool(topic, source_titles):
    """Real pictures of the whole story, most relevant first: each source
    article's lead image and every free picture it uses, the main article's
    Commons category (and its subcategories), then a Commons search on the
    event's name. A scene whose own search finds nothing draws from it
    before anything is generated (owner, 26 Oct: 35 of 57 scenes of the
    Israel nuclear video were generated while its article and category
    held real photographs; failure-point-bot draws on both)."""
    from media_sources import OFF_TOPIC
    out, seen = [], set()

    def add(cands):
        for c in cands or []:
            if not c or not c.get("url") or c["url"] in seen:
                continue
            if (c.get("year") or 0) > HISTORY_CUTOFF or OFF_TOPIC.search(c.get("title") or ""):
                continue
            import owner_media
            if owner_media.excluded(c.get("title")):
                continue
            seen.add(c["url"])
            out.append(c)

    for n, title in enumerate(t for t in source_titles or [] if t):
        try:
            add([article_lead_image(title)])
            add(article_images(title))
            if n == 0:
                cat = commons_category_of(title)
                if cat:
                    add(category_images(cat))
        except Exception as e:
            print(f"   ↩️  Pictures of {title!r} unavailable ({type(e).__name__}).")
    name = re.split(r"\s*\(|,|:\s| — |\?", topic or "", maxsplit=1)[0].strip()
    name = re.sub(r"^(the|does|did|how|why|what)\s+", "", name, flags=re.I)
    if name and len(name.split()) <= 6:
        try:
            add(search_archives(name, limit=20))
        except Exception:
            pass
    return out


def _pool_pick(pool, scene, used_urls):
    """The unused story picture that best matches what the scene says (its
    line and its search), else the next unused one."""
    want = _words(f"{scene.get('narration', '')} {scene.get('archive_query', '')}")
    best, best_score = None, -1
    for c in pool:
        if c.get("url") in used_urls:
            continue
        score = len(want & _words(f"{c.get('title', '')} {c.get('about', '')}"))
        if score > best_score:
            best, best_score = c, score
    return best


def _missing_place(scene, places):
    """The story place the scene's narration names but its archive_query
    does not, or None. "Leaders gathered in Bucharest" searched as "Palace
    of the Parliament 2008" drew London's Palace of Westminster (20 Oct): a
    landmark's generic name matches the famous one, not the story's."""
    text = (scene.get("narration") or "").lower()
    query = (scene.get("archive_query") or "").lower()
    for place in places or ():
        p = (place or "").strip()
        if p and re.search(rf"\b{re.escape(p.lower())}\b", text) and p.lower() not in query:
            return p
    return None


def generate_longform_visuals(scenes: list, output_dir: str = "build", topic: str = None,
                              places=None, source_titles=None):
    """Fills each scene with image_paths (1-3 frames), possibly a video_path
    (muted b-roll), and a credit string when a real archival photo was used.

    Returns (scenes, workers_ai_generated, total_generated) — the last two
    count only the AI-GENERATED frames (archival photos and b-roll are real
    content, not a Cloudflare-quota fallback, so they don't belong in this
    ratio). The caller uses it the same way main.py's check_visual_quality
    already does for Shorts: refuse to publish a video that came out almost
    entirely on the weaker fallback generator instead of shipping it silently.
    """
    print(f"🎨 Long-form visual engine ({LF_W}x{LF_H}, working at {KB_W}x{KB_H})...")
    os.makedirs(output_dir, exist_ok=True)

    api_calls, archival_used, broll_used, archival_video_used = 0, 0, 0, 0
    generated_sources = []
    last_broll_idx = -BROLL_MIN_GAP - 1
    last_archival_video_idx = -ARCHIVAL_VIDEO_MIN_GAP - 1
    # Nothing real may appear twice: one live run used the same C-130
    # photograph for two scenes and the same stock clip four times, which is
    # what "repetitive" looked like on screen.
    used_urls, used_broll_queries = set(), set()
    # How many times each recurring subject (the story's central figure,
    # typically) has already supplied a real photo — see
    # MAX_REAL_PHOTOS_PER_SUBJECT above for why used_urls alone (distinct
    # FILES) was not enough to stop a video reading as the same photo looping.
    subject_counts = {}
    # Every real frame obtained so far. When generation fails, a re-framed
    # archival image stands in — infinitely better than the black screen the
    # designed fallback produces.
    real_pool = []
    era0 = next((sc.get("era_year") for sc in scenes if sc.get("era_year")), None)
    import owner_media
    war_pool = _WarPool(topic, era0)
    story_pool = _story_pool(topic, source_titles)
    print(f"🗄️  {len(story_pool)} real picture(s) found for the story as a whole.")
    import owner_media
    prompts_out = []          # build/image_prompts.json: what was still generated

    for idx, scene in enumerate(scenes):
        wanted = _shots_for(scene.get("duration", 0))
        era = scene.get("era_year")
        images, credits = [], []

        # ---- The owner's own pictures for this scene come first ----
        mine = owner_media.scene_images(idx)
        if mine and not (scene.get("map") or scene.get("chain") or scene.get("dossier")):
            for shot, src in enumerate(mine[:max(wanted, 1)]):
                path = os.path.join(output_dir, f"lf_scene_{idx:03d}_{shot}.jpg")
                try:
                    Image.open(src).convert("RGB").save(path, "JPEG", quality=92)
                    _to_kb_size(path)
                    images.append(path)
                    credits.append(owner_media.credit_of(src))
                except Exception as e:
                    print(f"⚠️ Owner picture {src} unusable ({type(e).__name__}: {e}).")
            if images:
                print(f"🎨 Scene {idx}: the owner's picture(s) ({len(images)}).")
                scene["image_paths"], scene["image_path"] = images, images[0]
                scene["credit"] = credits
                continue

        # ---- 0) Real archival FOOTAGE, before any still ----
        # Tried first on a spaced set of plain scenes (maps, chains and the
        # decision-maker card draw their own pictures). Wikimedia Commons
        # first — every file with its own free licence and author — then the
        # public-domain newsreel collection. Any failure falls through to
        # the stills below.
        vquery = (scene.get("archive_query") or "").strip()
        if vquery and _missing_place(scene, places):
            vquery = f"{vquery} {_missing_place(scene, places)}"
        if (vquery and not (scene.get("map") or scene.get("chain")
                            or scene.get("dossier"))
                and archival_video_used < MAX_ARCHIVAL_VIDEO_CLIPS
                and idx - last_archival_video_idx > ARCHIVAL_VIDEO_MIN_GAP
                and scene.get("duration", 0) >= ARCHIVAL_VIDEO_MIN_SCENE):
            got = _archival_footage(vquery, era, idx, output_dir, used_urls)
            if not got and _is_war_scene(scene) and era == era0:
                try:
                    got = war_pool.segment(idx, output_dir, used_urls)
                except Exception as e:
                    print(f"   ↩️  War footage pool failed ({type(e).__name__}: {e}).")
            if got:
                video_path, poster, credit, label = got
                scene["video_path"] = video_path
                scene["image_path"] = poster
                scene["image_paths"] = [poster]
                scene["credit"] = [credit]
                scene["is_real"] = True
                archival_video_used += 1
                last_archival_video_idx = idx
                print(f"🎞️  Scene {idx}: real archival footage for "
                      f"{vquery!r} ({label})")
                continue

        # ---- 1) Real archival imagery from Wikimedia Commons ----
        # For post-photography stories: genuine photographs. For older ones:
        # period paintings, engravings and maps — filtered so a modern photo
        # of the place can never stand in for its fifteenth-century self.
        query = (scene.get("archive_query") or "").strip()
        subject_repeat = subject_counts.get(_subject_key(query), 0) if query else 0
        if query and subject_repeat >= MAX_REAL_PHOTOS_PER_SUBJECT:
            print(f"🔁 Scene {idx}: {query!r} already supplied "
                  f"{subject_repeat} real photos this video — skipping to "
                  f"keep the recurring subject from looping, trying other "
                  f"sources instead.")
            query = ""
        place = _missing_place(scene, places) if query else None
        if query:
            def _search(q):
                found = [c for c in search_archives(q)
                         if _era_appropriate(c, era) and c.get("url") not in used_urls
                         and not owner_media.excluded(c.get("title"))]
                if not place:
                    return found
                # the place named in the search, else in the photo's title:
                # never a same-named landmark somewhere else
                there = [c for c in search_archives(f"{q} {place}")
                         if _era_appropriate(c, era) and c.get("url") not in used_urls
                         and not owner_media.excluded(c.get("title"))]
                return there or [c for c in found
                                 if place.lower() in (c.get("title") or "").lower()]
            candidates = []
            # A repeat appearance of the same subject is steered toward a
            # different real moment first (a meeting, a ceremony) rather than
            # straight to the bare query, which tends to re-rank the same
            # narrow "default portrait" pool the first appearance already drew
            # from — technically distinct files, but visually near-identical.
            if subject_repeat > 0:
                qualifier = _SUBJECT_QUALIFIERS[
                    (subject_repeat - 1) % len(_SUBJECT_QUALIFIERS)]
                candidates = _search(f"{query} {qualifier}")
                if candidates:
                    print(f"🔀 Scene {idx}: subject's {subject_repeat + 1}. "
                          f"appearance — steered to {qualifier!r} for a "
                          f"different real photo of the same subject.")
            if not candidates:
                candidates = _search(query)
            if (not candidates and era and era < PHOTOGRAPHY_YEAR
                    and not any(a in query.lower() for a in ART_WORDS)):
                candidates = [c for c in search_commons(f"{query} painting")
                              if _era_appropriate(c, era)
                              and c.get("url") not in used_urls]
            if not candidates:
                widened = _widen_query(query)
                if widened:
                    candidates = _search(widened)
                    if candidates:
                        print(f"🔎 Scene {idx}: {query!r} found nothing — "
                              f"widened to {widened!r}")
            for cand in candidates:
                if len(images) >= wanted:
                    break
                path = os.path.join(output_dir,
                                    f"lf_scene_{idx:03d}_{len(images)}.jpg")
                if download_archival_image(cand, path, KB_W, KB_H):
                    images.append(path)
                    credits.append(format_credit(cand))
                    used_urls.add(cand.get("url"))
            if images:
                archival_used += len(images)
                scene["is_real"] = True
                real_pool.extend(images)
                subject_counts[_subject_key(query)] = subject_repeat + 1
                print(f"📜 Scene {idx}: {len(images)} archival image(s) "
                      f"for {query!r}")

        # ---- 1b) The story's own pictures, before anything is generated ----
        # Fills the shots the scene's own search left empty, best match to
        # the line first; each picture once in the whole video.
        while len(images) < wanted and story_pool:
            cand = _pool_pick(story_pool, scene, used_urls)
            if not cand:
                break
            used_urls.add(cand.get("url"))
            path = os.path.join(output_dir, f"lf_scene_{idx:03d}_{len(images)}.jpg")
            if download_archival_image(cand, path, KB_W, KB_H):
                images.append(path)
                credits.append(format_credit(cand))
                archival_used += 1
                scene["is_real"] = True
                real_pool.append(path)
                print(f"📜 Scene {idx}: a real picture of the story "
                      f"({cand.get('title', '')[:60]})")

        # ---- 2) Muted stock b-roll, timeless subjects only (optional) ----
        # Spaced through the video rather than granted first-come, so clips
        # and stills stay interleaved instead of clustering at the front.
        if (not images and pexels_available() and broll_used < MAX_BROLL_CLIPS
                and idx - last_broll_idx > BROLL_MIN_GAP
                and scene.get("duration", 0) >= MIN_BROLL_SCENE_SECONDS):
            broll_query = _timeless_broll_query(scene, used_broll_queries)
            video_path = os.path.join(output_dir, f"lf_broll_{idx:03d}.mp4")
            if broll_query and fetch_broll_video(broll_query, video_path,
                                                 min_duration=4.0):
                poster = os.path.join(output_dir, f"lf_scene_{idx:03d}_0.jpg")
                if extract_poster_frame(video_path, poster, KB_W, KB_H):
                    scene["video_path"] = video_path
                    scene["image_path"] = poster
                    scene["image_paths"] = [poster]
                    broll_used += 1
                    last_broll_idx = idx
                    used_broll_queries.add(broll_query)
                    print(f"🎥 Scene {idx}: muted b-roll for {broll_query!r}")
                    continue
                os.remove(video_path)

        # ---- 3) Generated frames fill whatever is still missing ----
        while len(images) < wanted:
            if api_calls:
                time.sleep(THROTTLE_SECONDS)
            shot = len(images)
            path = os.path.join(output_dir, f"lf_scene_{idx:03d}_{shot}.jpg")
            prompt = scene.get("image_prompt") or \
                "wide establishing shot, dark historical scene"
            print(f"🖼️  Scene {idx}: generating shot {shot + 1}/{wanted} "
                  f"(call {api_calls + 1})...")
            # download_image returns the generator's name ("workers-ai",
            # "pollinations/flux", ...) on success, not a bare bool — the
            # Shorts pipeline (main.py's check_visual_quality) already reads
            # this to refuse publishing a video that came out almost entirely
            # on the weaker fallback. This pipeline was discarding the exact
            # same string as a plain truthy check, so it had no equivalent
            # protection: a real run got 0/47 frames from Workers AI (the
            # free daily allocation was already spent by other runs) and
            # published anyway with nothing in the log calling that out.
            prompts_out.append({"scene": idx, "shot": shot,
                                "file": f"scene_{idx:03d}" + (f"_{shot}" if shot else "") + ".jpg",
                                "narration": scene.get("narration", ""),
                                "prompt": _vary_prompt(prompt, shot)})
            source = download_image(_vary_prompt(prompt, shot), path,
                                    width=LF_W, height=LF_H, era_year=era)
            if source:
                _to_kb_size(path)
            elif _reuse_real_frame(real_pool, path, len(images) + idx):
                # The generator being down must not turn the video into black
                # screens with subtitles, which is exactly what a run of thirty
                # HTTP 500s produced. A real photograph from elsewhere in the
                # video, punched in so it does not read as the same shot, keeps
                # something honest and watchable on screen.
                print(f"♻️  Scene {idx}: generation failed — re-framed an "
                      f"archival photo instead of a black frame.")
                source = "reused-archival"
            else:
                print(f"⚠️  Scene {idx}: generation failed — designed fallback.")
                make_fallback_image(path, seed=idx * 10 + shot,
                                    width=LF_W, height=LF_H)
                _to_kb_size(path)
                source = "designed-fallback"
            images.append(path)
            generated_sources.append(source)
            api_calls += 1

        scene["image_paths"] = images
        scene["image_path"] = images[0]
        if credits:
            scene["credit"] = credits

    if prompts_out:
        # the owner can make these and drop them in OWNER_MEDIA_DIR under
        # the "file" name; a re-run of the saved script puts them in place
        import json
        with open(os.path.join(output_dir, "image_prompts.json"), "w", encoding="utf-8") as f:
            json.dump(prompts_out, f, ensure_ascii=False, indent=1)
        print(f"📝 {len(prompts_out)} generated picture(s) listed in image_prompts.json.")
    total_frames = sum(len(s.get("image_paths") or []) for s in scenes)
    workers_ai_generated = sum(1 for s in generated_sources if s == "workers-ai")
    print(f"✅ Visuals complete: {total_frames} frames for {len(scenes)} scenes "
          f"({archival_used} archival, {api_calls} generated, "
          f"{archival_video_used} real archival video, "
          f"{broll_used} b-roll clips).")
    if api_calls and workers_ai_generated < api_calls:
        others = sorted(set(s for s in generated_sources if s != "workers-ai"))
        print(f"🎨 {workers_ai_generated}/{api_calls} generated frames from "
              f"Workers AI; the rest: {', '.join(others)}")
    return scenes, workers_ai_generated, api_calls


# Stock footage only exists in the present tense, so the only clips that can
# sit inside a period story are of things that look the same in any century.
# Keyed by subject words in the scene; the query on the right is what is
# actually sent to Pexels. Deliberately absent: people, soldiers, vehicles,
# cities — a story about the 1415 Portuguese invasion force once drew modern
# soldiers at a parade with a national flag in frame, which is exactly the
# clip that costs a channel its credibility.
TIMELESS_BROLL = [
    (("sea", "ocean", "ship", "fleet", "naval", "harbor", "harbour", "strait",
      "sail", "tide", "wave"), "dark rough sea waves"),
    (("storm", "clouds", "thunder", "rain"), "dark storm clouds timelapse"),
    (("fire", "burn", "flame", "smoke", "ash"), "flames burning in darkness"),
    (("map", "chart"), "antique map parchment"),
    (("letter", "document", "order", "treaty", "decree", "archive", "record",
      "ledger", "parchment", "scroll"), "old handwritten letter candlelight"),
    (("castle", "fortress", "wall", "fort", "citadel", "rampart", "gate"),
     "ancient stone fortress wall"),
    (("church", "cathedral", "monastery", "chapel"),
     "cathedral interior candles"),
    (("mountain", "valley", "cliff", "coast"), "misty mountains fog"),
    (("forest", "woods"), "dark foggy forest"),
    (("snow", "winter", "frost", "ice"), "snow falling darkness"),
    (("desert", "dune", "sand"), "desert dunes wind"),
    (("candle", "lantern", "torch", "night"), "candle flame flickering dark"),
]


def _timeless_broll_query(scene, used=()) -> str:
    """The Pexels query for a scene, or None when the scene has no unused
    timeless subject. None is the common and correct case: no b-roll beats
    wrong b-roll, and a repeat of one already on screen is the worst of all —
    'antique map parchment' was picked four times in one video."""
    text = (f"{scene.get('archive_query') or ''} "
            f"{scene.get('image_prompt') or ''}").lower()
    for keys, query in TIMELESS_BROLL:
        if query not in used and any(k in text for k in keys):
            return query
    return None
