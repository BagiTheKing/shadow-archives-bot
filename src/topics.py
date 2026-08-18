"""
Curated topic pool for The Archive Paradox.

Why this exists: letting the LLM pick its own topic every day produces the same
5 famous stories over and over, which makes the channel feel mass-produced.
A curated pool + date-based rotation guarantees months of unique, verifiably
real stories before any repeat.

WHY THIS FILE WAS RESTRUCTURED (July 2026)
------------------------------------------
The first 29 uploads showed a clear pattern. Everything that broke 700 views was
the same kind of story: one decision or one error that destroyed something big.

    Charge of the Light Brigade   1,058 views
    The Petersburg Crater         1,052 views
    The order that killed 600       892 views
    The Darien Scheme               724 views

Everything that flopped was general curiosity trivia — separated twins (35),
duelling comic strips (5), a haunted mountain pass (4). Same production quality,
same cadence, ~200x difference in reach. The channel does not have a quality
problem, it has an identity problem.

So the pool is now split in two:

  CORE_TOPICS       — the proven lane: a catastrophic decision, order, design
                      flaw or miscalculation, and the disaster it caused.
                      This is what the channel is *about*.
  EXPERIMENT_TOPICS — everything else, kept for one slot in every seven so the
                      channel can still discover a second lane without diluting
                      the first.

Also: every topic already covered by the first 29 uploads has been removed. The
old pool was nearly exhausted, which is part of why recent uploads drifted into
the weakest material.

Add your own topics — the more specific, the better the script. Put anything
that fits "one mistake, one catastrophe" in CORE_TOPICS.
"""

import datetime
import os

# One experiment topic every N slots. Set to 0 to publish nothing but the core
# niche, which is the default now.
#
# WHY THE DEFAULT IS 0 (August 2026)
# ----------------------------------
# The channel is running two identities at once and the algorithm cannot profile
# it. Thirty days of nothing but catastrophe-decision stories is the cheapest way
# to find out whether a single identity performs better than a split one.
#
# Honest caveat, because the numbers do not fully support switching the lane off
# permanently: the single most-liked video on the channel is an experiment topic
# (the Antikythera Mechanism, 588 views / 32 likes — more likes than any
# catastrophe Short). The experiment lane has a worse average, not a worse
# ceiling. So this is a deliberate 30-day test, not a verdict, and restoring it
# is one line: set EXPERIMENT_EVERY=7 in the workflow env.
EXPERIMENT_EVERY = int(os.getenv("EXPERIMENT_EVERY", "0"))

# ---------------------------------------------------------------------------
# REACH: how much of an audience a subject can find before the script does any
# work. This is the gate that moves the hit rate, and it is not the same axis as
# catastrophe-vs-mystery.
#
# 7 of the first 42 Shorts cleared 500 views; the rest died between 5 and 150 —
# same pipeline, same format, a 100x spread. The split tracks the SUBJECT:
#
#   WIDE       Recognised on sight, or shocking without any prior knowledge, and
#              not already a Shorts cliché. HMS Hood 605, Ariane 5 529, the
#              Crater 1,054, the Light Brigade 1,059.
#   NARROW     Real, well documented, and nobody is searching for it. The hook
#              has to do all the work and usually cannot. Zeebrugge 65 views in
#              18 days, Gimli Glider 85 in 8, Anjouan 19, the Meknes siege 29.
#   SATURATED  Famous, and done a thousand times by better-funded channels.
#              Recognition does not help when the feed already has fifty
#              versions. Chernobyl 38 views in 15 days, Voynich 12 in 16,
#              Dyatlov Pass 5 in 20 — all maximally famous, all dead on arrival.
#
# HOW A TOPIC MAY BE RATED FROM THE CHANNEL'S OWN NUMBERS
# ------------------------------------------------------
# Only from a video at least MIN_RATING_AGE_DAYS old. This rule exists because
# the first version of this file broke it: the Great Fire of London was rated
# SATURATED — and called the strongest evidence for the whole idea — on two
# uploads that were TWENTY-FOUR HOURS OLD. A day-old video has barely any views
# yet by definition, so rating on raw totals stamps SATURATED on everything the
# channel just published, and the pool decays toward NARROW on its own. The gate
# would poison its own inputs. The Great Fire is WIDE again on recognition
# alone, and it will not be re-rated until its uploads are old enough to mean
# something.
#
# Views-per-hour does not rescue an immature video either, despite looking like
# the age-neutral fix. It is a LIFETIME average and Shorts front-load their
# views, so it decays as a video ages: HMS Hood (605 views, 5.8 days) reads 4.35
# vph and the Antikythera Mechanism (588 views, 18.8 days) reads 1.30, for the
# same audience. VPH is biased by age too, just in the opposite direction from
# raw views. Compare within an age band or wait; do not mix.
#
# So: the ratings on Chernobyl, Voynich, Zeebrugge, Gimli, Ariane 5, Hyatt
# Regency and Challenger are measurements from mature uploads. Everything else
# is editorial judgement — my read of how recognisable a subject is and how
# heavily Shorts already covers it — and the Darien Scheme (obscure, 725 views)
# is the standing reminder that the rule has exceptions. topic_reach.py measures
# the saturation half properly at run time; these ratings are the offline prior
# for when it cannot.

# A video younger than this says nothing about its topic. Nothing in this
# repository may rate, rank or judge a subject on an upload below this age.
MIN_RATING_AGE_DAYS = 7

WIDE = "wide"
NARROW = "narrow"
SATURATED = "saturated"

# Tiers this pipeline may publish, best first. SATURATED is never selected
# automatically — set REACH_FLOOR=all to override.
REACH_ORDER = [WIDE, NARROW, SATURATED]
REACH_FLOOR = os.getenv("REACH_FLOOR", WIDE)

_CORE = [
    # --- Orders and decisions that destroyed armies ---
    ("The Battle of Karansebes (1788), where the Austrian army attacked itself in the dark and took hundreds of casualties without ever meeting the enemy", SATURATED),
    ("Operation Cottage (1943), where Allied troops stormed Kiska island and took over 300 casualties fighting each other, because the Japanese had left two weeks earlier", WIDE),
    ("The Battle of Isandlwana (1879), where a British force with rifles and artillery was destroyed by Zulu infantry after the camp was left unfortified", WIDE),
    ("The Battle of Little Bighorn (1876) and Custer's decision to split his regiment into three before attacking a village he had never scouted", WIDE),
    ("Operation Market Garden (1944), the airborne plan that depended on one road and ignored reconnaissance photos showing German armour at Arnhem", WIDE),
    ("The Dieppe Raid (1942), where tanks were landed onto shingle beach that seized their tracks within metres of the waterline", WIDE),
    ("The Gallipoli landings (1915) and the ANZAC troops put ashore beneath cliffs, roughly a mile from the flat beach they had trained for", WIDE),
    ("The first day of the Somme (1916), when British troops were ordered to walk toward German lines and took 57,470 casualties before evening", WIDE),
    ("Task Force Smith (1950), the undersized American unit sent to stop a tank column with bazooka rounds that could not penetrate a T-34", NARROW),
    ("The Battle of Cannae (216 BC), where Hannibal used Rome's own numerical advantage to encircle and annihilate the larger army", WIDE),
    ("The Battle of Agincourt (1415), where French numbers became a liability in a funnel of mud between two woods", WIDE),
    ("The Battle of Tanga (1914), the British landing in East Africa broken up in part by swarms of disturbed bees", WIDE),
    ("The Siege of Cartagena de Indias (1741), where Admiral Vernon struck victory medals before the battle and then lost it", WIDE),
    ("The Battle of Adwa (1896), the Italian defeat that grew out of two versions of the Treaty of Wuchale saying different things in different languages", NARROW),
    ("Italy's invasion of Greece (1940), launched in mountain winter without winter equipment and immediately pushed backwards", NARROW),
    ("Operation Barbarossa (1941) and the decision not to issue winter clothing because the campaign was expected to end before the cold", WIDE),
    ("The voyage of the Russian Baltic Fleet to Tsushima (1904), which opened fire on British fishing trawlers in the North Sea believing them to be Japanese torpedo boats", WIDE),
    ("Operation Eagle Claw (1980), the Iran hostage rescue abandoned in a sandstorm that ended with a helicopter and a transport colliding on the ground", NARROW),
    ("The Bay of Pigs invasion (1961) and the assumptions about a popular uprising that never came", WIDE),
    ("The Zeebrugge Raid (1918), the blockships that were scuttled in the wrong position and left the canal usable", NARROW),

    # --- Engineering decisions that killed ---
    ("The Challenger disaster (1986) and the engineers who argued against launching in the cold the night before", WIDE),
    ("The Chernobyl safety test (1986), the experiment that disabled the protections it was meant to verify", SATURATED),
    ("The Tacoma Narrows Bridge (1940), the span designed for wind loads that destroyed itself in a 40 mph breeze", SATURATED),
    ("The Hyatt Regency walkway collapse (1981), where a single change to how two walkways hung doubled the load on a connection and killed 114 people", WIDE),
    ("The Johnstown Flood (1889), the dam a private club lowered and modified until it failed and killed 2,209 people downstream", WIDE),
    ("The Ariane 5 Flight 501 explosion (1996), destroyed 39 seconds after launch by reused software converting a number into a variable too small to hold it", WIDE),
    ("The Mars Climate Orbiter (1999), lost because one team worked in pound-force and the other in newtons", WIDE),
    ("The Gimli Glider (1983), Air Canada Flight 143, the airliner that ran out of fuel at 41,000 feet after a conversion between pounds and kilograms", NARROW),
    ("The Tenerife airport disaster (1977), the deadliest crash in aviation history, caused by an ambiguous radio exchange in fog", WIDE),
    ("The sinking of HMS Hood (1941), the battlecruiser lost in about three minutes with 1,415 men and three survivors", WIDE),
    ("The loss of the Kursk (2000), the submarine sunk by its own torpedo propellant leaking in the tube", WIDE),
    ("The General Slocum fire (1904), where 1,021 people died beside life preservers that had rotted in place for thirteen years", NARROW),
    ("The Sultana explosion (1865), the worst maritime disaster in American history, caused by a boiler patched overnight on a ship carrying six times its legal capacity", NARROW),
    ("The Great Fire of London (1666) and the Lord Mayor who looked at the first burning street and went back to bed", WIDE),
    ("The Boston Molasses Flood (1919), the storage tank that had been leaking so visibly it was painted brown to hide it", SATURATED),
    ("The London Beer Flood (1814), when a single failed iron hoop released a wave of porter through a London parish", NARROW),
    ("The Vajont Dam disaster (1963), where a perfect dam held and the mountain beside it did not", WIDE),
    ("The Banqiao Dam failure (1975), designed against a once-in-a-thousand-year storm that arrived within a decade", NARROW),
    ("The Texas City disaster (1947), started by a cargo fire that spectators gathered on the docks to watch", NARROW),
    ("The Halifax harbour collision (1917) and the twenty minutes when both crews knew what was coming and the city did not", WIDE),
]

_EXPERIMENT = [
    ("The Antikythera Mechanism, the 2,000-year-old geared computer found in a shipwreck", WIDE),
    ("Roman dodecahedrons, the bronze objects found across Europe with no surviving explanation of their use", NARROW),
    ("The Voynich Manuscript, the illustrated book written in a script no one has decoded", SATURATED),
    ("The Wow! Signal (1977), the 72-second radio burst never detected again", SATURATED),
    ("The Mary Celeste (1872), the ship found under sail in good order with its entire crew gone", SATURATED),
    ("The Lost Colony of Roanoke and the single word carved into a post", SATURATED),
    ("The Tunguska Event (1908), the blast that flattened 80 million trees and left no crater", WIDE),
    ("The year 536 AD, described by historians as the worst year to be alive", WIDE),
    ("The Carrington Event (1859), the solar storm that set telegraph offices on fire", WIDE),
    ("The disappearance of the Ninth Roman Legion and the competing explanations", NARROW),
    ("D.B. Cooper, the only unsolved airplane hijacking in American history", SATURATED),
    ("The Somerton Man, the unidentified body found with a torn page from a Persian poem", NARROW),
    ("The Max Headroom broadcast intrusion (1987), the hijacked television signal never traced", SATURATED),
    ("The Isdal Woman, found in a Norwegian valley with every label removed from her clothing", NARROW),
    ("Kaspar Hauser, the boy who appeared in Nuremberg in 1828 claiming he had grown up in a dark cell", NARROW),
    ("The Amber Room, the chamber looted in 1941 and never recovered", NARROW),
    ("The Library of Alexandria and what was actually lost, against the myth", SATURATED),
    ("Genghis Khan's hidden tomb and why Mongolia has not looked for it", WIDE),
    ("The Phaistos Disc, the Bronze Age artifact stamped with symbols used nowhere else", NARROW),
    ("The Eilean Mor lighthouse keepers, three men gone from a locked island station in 1900", SATURATED),
    ("Agatha Christie's eleven-day disappearance in 1926, which she never explained", NARROW),
    ("The Hinterkaifeck murders (1922), the farm where the killer appears to have lived in the attic before acting", SATURATED),
]


# Topics already covered by the Shorts channel. Excluded from the rotation so
# they never repeat as Shorts — but kept here on purpose, because the long-form
# encore lane works by expanding whichever Short performed best, and that Short
# is by definition one of these. Without this list channel_insights can never
# match a title to a topic and the lane silently falls back to the rotation.
PUBLISHED_TOPICS = [
    "The Charge of the Light Brigade (1854) and the ambiguous order that sent cavalry into a valley of cannons",
    "The Siege of Petersburg crater disaster (1864), where Union troops charged into the hole they had just blown",
    "The Darien Scheme (1698), Scotland's colonial gamble that bankrupted the nation and led to union with England",
    "The Zimmermann Telegram, the intercepted message that pulled America into WWI",
    "The Battle of New Orleans (1815), fought two weeks after the war had officially ended",
    "Napoleon's invasion of Russia and the mathematics of how 400,000 soldiers became 10,000",
    "The sinking of the Vasa (1628), Sweden's mightiest warship that sank 20 minutes into its maiden voyage",
    "Operation Eiche and the glider rescue of Mussolini that succeeded despite absurd risks",
    "The novel 'Futility' (1898) that described the Titanic disaster 14 years before it happened",
    "Lincoln and Kennedy assassination parallels, and which ones are actually true",
    "The Maginot Line and how Germany simply went around France's 'impenetrable' defenses in 1940",
    "The Emu War (1932), when Australia deployed machine guns against birds and lost",
    "The Halifax Explosion (1917), the largest man-made blast before the atomic bomb",
    "Operation Mincemeat (1943), the corpse with fake documents that fooled Hitler",
    "The Dancing Plague of 1518, when hundreds of people danced themselves to exhaustion in Strasbourg",
    "The Christmas Truce of 1914, when enemies played football between the trenches",
    "The Ghost Army of WWII, the American unit of artists that fooled Germany with inflatable tanks",
    "The Jim Twins, separated at birth, who lived nearly identical lives without knowing each other",
    "The Battle of Los Angeles (1942), when the US fired 1,400 shells at nothing",
    "The Trojan Horse of Meknes and other lesser-known deception tactics in siege warfare",
    "The Millennium Bridge wobble and other engineering marvels that failed on day one",
    "Spain's Anjouan expedition mishaps and other invasions launched on wrong intelligence",
    "Tsutomu Yamaguchi, the man who survived both Hiroshima and Nagasaki",
    "The curious case of the two Dennis the Menace comics debuting the same week on opposite sides of the Atlantic",
    "The Dyatlov Pass incident (1959) and the leading scientific explanation for the hikers' deaths",
    "The Antikythera Mechanism, the 2,000-year-old geared computer found in a shipwreck",
    "The Zeebrugge Raid (1918), the blockships that were scuttled in the wrong position",
]

# Everything the encore lane may match a published title against.
CORE_TOPICS = [t for t, _ in _CORE]
EXPERIMENT_TOPICS = [t for t, _ in _EXPERIMENT]

# Every rated topic, by rating. Anything absent (the already-published entries
# below) is treated as NARROW, which is the cautious answer.
REACH = {t: r for t, r in _CORE + _EXPERIMENT}

ALL_TOPICS = CORE_TOPICS + EXPERIMENT_TOPICS + PUBLISHED_TOPICS


def reach_of(topic: str) -> str:
    """A topic's tier. Unrated entries — the already-published ones — count as
    NARROW, so nothing sneaks into the wide lane by being absent from REACH."""
    return REACH.get(topic, NARROW)


def _allowed_tiers(floor: str) -> set:
    """Every tier from the best down to `floor`. 'all' means no gate."""
    if floor == "all":
        return set(REACH_ORDER)
    if floor not in REACH_ORDER:
        print(f"⚠️ Unknown REACH_FLOOR {floor!r} — falling back to {WIDE!r}.")
        floor = WIDE
    return set(REACH_ORDER[:REACH_ORDER.index(floor) + 1])


def _topic_for_slot(slot: int) -> str:
    """The pool entry a given rotation slot maps to."""
    if EXPERIMENT_EVERY and slot % EXPERIMENT_EVERY == 0 and EXPERIMENT_TOPICS:
        return EXPERIMENT_TOPICS[(slot // EXPERIMENT_EVERY) % len(EXPERIMENT_TOPICS)]

    # Subtract the slots already spent on experiments so the core pool advances
    # by exactly one each time it is used, instead of skipping every 7th entry.
    core_slot = slot - (slot // EXPERIMENT_EVERY if EXPERIMENT_EVERY else 0)
    return CORE_TOPICS[core_slot % len(CORE_TOPICS)]


def _rotate(slot: int, exclude: set, tiers: set):
    """The first topic at or after `slot` that is unpublished and in `tiers`."""
    for step in range(len(CORE_TOPICS) + len(EXPERIMENT_TOPICS) + 1):
        topic = _topic_for_slot(slot + step)
        if topic in exclude or reach_of(topic) not in tiers:
            continue
        if step:
            print(f"↪️  Skipped {step} pool entr(ies): already published, or "
                  f"below the {'/'.join(sorted(tiers))} reach floor.")
        return topic
    return None


def pick_topic(offset: int = 0, exclude=(), floor: str = None) -> str:
    """The topic for today's slot: unpublished, and worth publishing.

    Two gates, and they are different problems.

    Deduplication (`exclude`) stops the channel republishing itself. The date
    rotation alone is not memory: two runs on the same day land on the same slot
    and therefore the same subject, which is how four Light Brigade Shorts, two
    Ariane 5s and two Great Fires happened.

    Reach (`floor`) is the hit-rate gate. Nothing about the pipeline separates a
    600-view upload from a 10-view one — the scripts, the voice and the render
    are identical. What separates them is whether anyone was ever going to
    watch a video about that subject. Topics below the floor stay in the pool
    and are used only once the better ones are gone, rather than being deleted:
    a NARROW topic is not a bad topic, it is one that needs help this channel
    cannot give it yet.

    Returns None only when the pool is genuinely finished at every tier.
    """
    exclude = {e.strip() for e in exclude if isinstance(e, str) and e.strip()}
    slot = datetime.date.today().toordinal() + offset
    floor = floor or REACH_FLOOR

    topic = _rotate(slot, exclude, _allowed_tiers(floor))
    if topic:
        return topic

    # Nothing left in the preferred tiers. Widening beats publishing nothing,
    # but say so plainly — it means the pool needs new WIDE entries.
    for wider in REACH_ORDER[REACH_ORDER.index(NARROW):]:
        if wider == SATURATED and floor != "all":
            break
        topic = _rotate(slot, exclude, _allowed_tiers(wider))
        if topic:
            print(f"⚠️ No unpublished {WIDE} topic left — falling back to "
                  f"{wider}. Add recognisable subjects to CORE_TOPICS.")
            return topic

    print("❌ Every topic in the pool has already been published.")
    return None


if __name__ == "__main__":
    # Preview the next three weeks of scheduling.
    # Each preview day excludes what the days before it published, which is what
    # published_history does at runtime. Without that the preview shows the same
    # topic twice whenever a skip lands on it from two different slots.
    published = set()
    for i in range(21):
        day = datetime.date.today() + datetime.timedelta(days=i)
        picked = pick_topic(i, exclude=published)
        tag = reach_of(picked) if picked else "-"
        print(f"{day} [{tag:9}] {(picked or '(pool exhausted)')[:80]}")
        published.add(picked)
