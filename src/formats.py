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

    THE ORDER     someone chose wrong, and other people paid
    THE DESIGN    the thing was doomed before anyone touched it
    THE UNSOLVED  no decision, no villain, and still no answer

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
