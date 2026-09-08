"""
The channel's three recurring episode types.

The problem this solves: the pool is varied — the Emu War, the Dancing Plague,
the Jim Twins, a Bronze Age computer — and every one of them was narrated as
"one decision and what it cost", because that was the only frame the writer had.
The Dancing Plague is not a decision. The Antikythera Mechanism is not a
disaster. Forcing them through that arc produced videos that promised the same
thing fifty times and then did not deliver it.

So the frame is no longer one frame. Each format is a different promise, kept
inside one channel:

    THE ORDER      someone chose wrong, and other people paid
    THE DESIGN     the thing was doomed before anyone touched it
    THE UNSOLVED   no decision, no villain, and still no answer
    THE REGIME     the men who ran the century, and the ones who defied them
    THE FAULT LINE the decision that drew a line people are still dying on
    THE LAW        what a religion or a court actually ruled, and what it cost

WHY THREE LANES BECAME SIX (August 2026)
----------------------------------------
The first six episodes under the rewritten writing rules produced genuinely
good scripts — the Cartagena Short lands a real mechanism (ladders measured for
the wall but not the ditch below) and a real kicker (the king making it illegal
to wear the victory medals). It went nowhere, because nobody is looking for
Cartagena. Craft was never the ceiling; the subject was.

The three original lanes are all one thing: an obscure logistical accident.
Nothing in them is a subject a person already cares about before the video
starts. The three new lanes are, and they are the three places where the
audience for "history that explains something" actually lives:

    THE REGIME      the most-searched subject in all of history video
    THE FAULT LINE  why the news looks the way it does
    THE LAW         belief and law, which people argue about for free

THE FAULT LINE is deliberately the ROOTS of a live conflict, never the live
conflict itself. That is an editorial choice AND a safety one. This pipeline
writes with free models that hallucinate, renders with AI imagery, and uploads
without a human reading the script first. A wrong number about a 1741 siege is
embarrassing; a wrong number about a war being fought this week is defamatory,
and synthetic footage of it is worse. The 1916 map, the 1917 letter and the
1953 coup are documented, checkable, evergreen, and they are what the audience
is actually missing — the news supplies the events and never the cause.

A format is worth having only if a viewer can see it, so each one carries an
accent colour and a name on the thumbnail. Three episodes of THE DESIGN in a
row should read as a set at a glance, and a viewer who liked one should be able
to find the others without reading a word.

Adding a fourth: add an entry here, point the topics at it, and nothing else
needs to change. The accent must stay legible on a dark, desaturated frame —
these are all high-value colours for that reason.
"""

# Accent colours are RGB, and each is the one word the eye lands on in the
# thumbnail plus the rule above the headline.
FORMATS = {
    "order": {
        "name": "THE ORDER",
        "accent": (255, 216, 0),        # amber — the channel's original accent
        "promise": "someone chose wrong, and other people paid",
        "brief": (
            "This is an ORDER story: a decision made by a named person, and the "
            "people who paid for it. The viewer must finish knowing WHO chose, "
            "WHAT they knew at the time, and WHAT it cost. Be fair to them — the "
            "decision has to look defensible at the moment it was made, or the "
            "story is just someone being stupid and there is nothing to learn. "
            "Never end on a moral."),
        "tags": ["military history", "military blunders", "history documentary",
                 "command decisions"],
        # The three that render above the title. Chosen per format because
        # YouTube counts the first three hashtags anywhere in the description
        # and shows them as the video's visible category.
        "hashtags": ["#history", "#militaryhistory", "#disaster"],
    },
    "design": {
        "name": "THE DESIGN",
        "accent": (255, 92, 60),        # signal red — failure, heat, alarm
        "promise": "the thing was doomed before anyone touched it",
        "brief": (
            "This is a DESIGN story: the flaw was built in, and the disaster was "
            "waiting for a day to happen on. The viewer must finish knowing "
            "exactly WHAT was wrong with the object and WHY nobody caught it — "
            "name the component, the tolerance, the unit, the number. The people "
            "here are usually not villains; they are engineers who were "
            "overruled, or who did not have the one measurement that mattered. "
            "The object is the antagonist."),
        "tags": ["engineering failure", "engineering disasters",
                 "structural failure", "how it failed"],
        "hashtags": ["#engineering", "#disaster", "#history"],
    },
    "unsolved": {
        "name": "THE UNSOLVED",
        "accent": (86, 214, 255),       # cold cyan — reads as archive, not alarm
        "promise": "no decision, no villain, and still no answer",
        "brief": (
            "This is an UNSOLVED story: there is no decision to blame and no "
            "disaster to total up. Do NOT force one. The shape is the anomaly, "
            "then everything that has been ruled out, then the single detail "
            "that keeps it open. State plainly what is actually known versus "
            "what is speculated — the channel's credibility is the whole product "
            "here. End on the open question, not on an answer you invented."),
        "tags": ["unsolved mysteries", "historical mysteries", "archaeology",
                 "unexplained"],
        "hashtags": ["#unsolved", "#mystery", "#history"],
    },
    "regime": {
        "name": "THE REGIME",
        "accent": (168, 85, 247),       # violet — power, not alarm
        "promise": "the men who ran the century, and the ones who defied them",
        "brief": (
            "This is a REGIME story: a dictatorship, its machinery, and the "
            "people inside it — the ones who served it and the ones who tried "
            "to stop it. The viewer must finish knowing WHO did the thing, "
            "WHAT they actually did, and what it cost them or everyone else. "
            "Two hard rules. First, never make the regime look impressive; "
            "name what it did to people, plainly, without dwelling on gore. "
            "Second, never invent a quotation or a motive — say what is "
            "documented and let it be as bad as it was. Where a person "
            "resisted, their courage IS the story; give them their name."),
        "tags": ["world war 2", "ww2 history", "nazi germany",
                 "history documentary", "wwii"],
        "hashtags": ["#ww2", "#history", "#worldwar2"],
    },
    "faultline": {
        "name": "THE FAULT LINE",
        "accent": (16, 224, 137),       # emerald — a map line, not a warning
        "promise": "the decision that drew a line people are still dying on",
        "brief": (
            "This is a FAULT LINE story: one meeting, map, treaty or coup that "
            "shaped a conflict the viewer has seen in the news. The viewer "
            "must finish understanding a headline they did not understand "
            "before. Rules, and they are not negotiable. Stop at the HISTORICAL "
            "decision — describe what was signed, drawn or overthrown, and its "
            "documented consequences. Do NOT narrate current events, casualty "
            "figures from an ongoing war, or who is right today. Name what each "
            "side was promised and by whom, in the same even tone; a viewer on "
            "either side must finish feeling the account was fair. No "
            "present-day call to action, no verdict on a living conflict. End "
            "on the line that was drawn, not on what should happen now."),
        "tags": ["middle east history", "history explained", "geopolitics",
                 "colonial history", "history documentary"],
        "hashtags": ["#history", "#geopolitics", "#middleeast"],
    },
    "law": {
        "name": "THE LAW",
        "accent": (226, 200, 154),      # parchment — a document, not a siren
        "promise": "what a religion or a court actually ruled, and what it cost",
        "brief": (
            "This is a LAW story: one real ruling, rule or trial from a "
            "religious or legal tradition — Islamic, Jewish, canon, or secular "
            "— and what it meant for actual people. The viewer must finish able "
            "to state the rule and why it existed. Rules: describe what the "
            "tradition ITSELF holds and cite the court, council or text by "
            "name; never characterise a faith by its worst moment or its best "
            "one alone. Where a ruling protected someone, say so; where it "
            "destroyed someone, say that too, with the same directness. You are "
            "explaining a system of thought to someone outside it, not "
            "prosecuting or defending it. Never tell the viewer what to "
            "believe, and never compare two faiths to rank them."),
        "tags": ["religious history", "history of law", "legal history",
                 "comparative religion", "history documentary"],
        "hashtags": ["#history", "#religion", "#law"],
    },
}

DEFAULT_FORMAT = "order"


def get(key: str) -> dict:
    """The format's config, falling back to ORDER rather than raising. A topic
    that slipped through untagged must still produce a video."""
    return FORMATS.get(key or "", FORMATS[DEFAULT_FORMAT])


def name_of(key: str) -> str:
    return get(key)["name"]


def accent_of(key: str) -> tuple:
    return get(key)["accent"]


def brief_of(key: str) -> str:
    return get(key)["brief"]


def tags_of(key: str) -> list:
    return list(get(key)["tags"])


def hashtags_of(key: str) -> list:
    """The three that render above the title. YouTube counts the first three
    hashtags found anywhere in the description, so they are worth choosing
    rather than letting the writer scatter its own through the text."""
    return list(get(key)["hashtags"])


# A live run reached the very last step of the pipeline — script, narration,
# images, video, thumbnail, description all already built — and had YouTube's
# upload API reject the whole thing with "invalidTags" and no indication of
# which tag was the problem. The list-wide 500-character budget below already
# existed; what was missing is YouTube's OTHER two tag rules: no single tag
# over 100 characters, and neither '<' nor '>' anywhere in one. Both are easy
# for a free-form LLM tag (script.get("tags")) to violate by accident — the
# channel's own fixed tags above are hand-written and already clean.
_TAG_MAX_LEN = 100
_TAG_LIST_BUDGET = 500


def sanitize_tags(tags: list) -> list:
    """The final YouTube-safe tag list: stripped, deduped in order, and
    validated against YouTube's actual tag rules before anything is spent
    building a video around them."""
    seen, cleaned = set(), []
    for t in tags:
        if not isinstance(t, str):
            continue
        t = t.strip()
        if not t or t in seen or len(t) > _TAG_MAX_LEN or "<" in t or ">" in t:
            continue
        seen.add(t)
        cleaned.append(t)

    trimmed, budget = [], _TAG_LIST_BUDGET
    for t in cleaned:
        cost = len(t) + 1
        if cost > budget:
            break
        trimmed.append(t)
        budget -= cost
    return trimmed
