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

CORE_TOPICS = [
    # --- Orders and decisions that destroyed armies ---
    "The Battle of Karansebes (1788), where the Austrian army attacked itself in the dark and took hundreds of casualties without ever meeting the enemy",
    "Operation Cottage (1943), where Allied troops stormed Kiska island and took over 300 casualties fighting each other, because the Japanese had left two weeks earlier",
    "The Battle of Isandlwana (1879), where a British force with rifles and artillery was destroyed by Zulu infantry after the camp was left unfortified",
    "The Battle of Little Bighorn (1876) and Custer's decision to split his regiment into three before attacking a village he had never scouted",
    "Operation Market Garden (1944), the airborne plan that depended on one road and ignored reconnaissance photos showing German armour at Arnhem",
    "The Dieppe Raid (1942), where tanks were landed onto shingle beach that seized their tracks within metres of the waterline",
    "The Gallipoli landings (1915) and the ANZAC troops put ashore beneath cliffs, roughly a mile from the flat beach they had trained for",
    "The first day of the Somme (1916), when British troops were ordered to walk toward German lines and took 57,470 casualties before evening",
    "Task Force Smith (1950), the undersized American unit sent to stop a tank column with bazooka rounds that could not penetrate a T-34",
    "The Battle of Cannae (216 BC), where Hannibal used Rome's own numerical advantage to encircle and annihilate the larger army",
    "The Battle of Agincourt (1415), where French numbers became a liability in a funnel of mud between two woods",
    "The Battle of Tanga (1914), the British landing in East Africa broken up in part by swarms of disturbed bees",
    "The Siege of Cartagena de Indias (1741), where Admiral Vernon struck victory medals before the battle and then lost it",
    "The Battle of Adwa (1896), the Italian defeat that grew out of two versions of the Treaty of Wuchale saying different things in different languages",
    "Italy's invasion of Greece (1940), launched in mountain winter without winter equipment and immediately pushed backwards",
    "Operation Barbarossa (1941) and the decision not to issue winter clothing because the campaign was expected to end before the cold",
    "The voyage of the Russian Baltic Fleet to Tsushima (1904), which opened fire on British fishing trawlers in the North Sea believing them to be Japanese torpedo boats",
    "Operation Eagle Claw (1980), the Iran hostage rescue abandoned in a sandstorm that ended with a helicopter and a transport colliding on the ground",
    "The Bay of Pigs invasion (1961) and the assumptions about a popular uprising that never came",
    "The Zeebrugge Raid (1918), the blockships that were scuttled in the wrong position and left the canal usable",

    # --- Engineering decisions that killed ---
    "The Challenger disaster (1986) and the engineers who argued against launching in the cold the night before",
    "The Chernobyl safety test (1986), the experiment that disabled the protections it was meant to verify",
    "The Tacoma Narrows Bridge (1940), the span designed for wind loads that destroyed itself in a 40 mph breeze",
    "The Hyatt Regency walkway collapse (1981), where a single change to how two walkways hung doubled the load on a connection and killed 114 people",
    "The Johnstown Flood (1889), the dam a private club lowered and modified until it failed and killed 2,209 people downstream",
    "The Ariane 5 Flight 501 explosion (1996), destroyed 39 seconds after launch by reused software converting a number into a variable too small to hold it",
    "The Mars Climate Orbiter (1999), lost because one team worked in pound-force and the other in newtons",
    "The Gimli Glider (1983), Air Canada Flight 143, the airliner that ran out of fuel at 41,000 feet after a conversion between pounds and kilograms",
    "The Tenerife airport disaster (1977), the deadliest crash in aviation history, caused by an ambiguous radio exchange in fog",
    "The sinking of HMS Hood (1941), the battlecruiser lost in about three minutes with 1,415 men and three survivors",
    "The loss of the Kursk (2000), the submarine sunk by its own torpedo propellant leaking in the tube",
    "The General Slocum fire (1904), where 1,021 people died beside life preservers that had rotted in place for thirteen years",
    "The Sultana explosion (1865), the worst maritime disaster in American history, caused by a boiler patched overnight on a ship carrying six times its legal capacity",
    "The Great Fire of London (1666) and the Lord Mayor who looked at the first burning street and went back to bed",
    "The Boston Molasses Flood (1919), the storage tank that had been leaking so visibly it was painted brown to hide it",
    "The London Beer Flood (1814), when a single failed iron hoop released a wave of porter through a London parish",
    "The Vajont Dam disaster (1963), where a perfect dam held and the mountain beside it did not",
    "The Banqiao Dam failure (1975), designed against a once-in-a-thousand-year storm that arrived within a decade",
    "The Texas City disaster (1947), started by a cargo fire that spectators gathered on the docks to watch",
    "The Halifax harbour collision (1917) and the twenty minutes when both crews knew what was coming and the city did not",
]

EXPERIMENT_TOPICS = [
    "The Antikythera Mechanism, the 2,000-year-old geared computer found in a shipwreck",
    "Roman dodecahedrons, the bronze objects found across Europe with no surviving explanation of their use",
    "The Voynich Manuscript, the illustrated book written in a script no one has decoded",
    "The Wow! Signal (1977), the 72-second radio burst never detected again",
    "The Mary Celeste (1872), the ship found under sail in good order with its entire crew gone",
    "The Lost Colony of Roanoke and the single word carved into a post",
    "The Tunguska Event (1908), the blast that flattened 80 million trees and left no crater",
    "The year 536 AD, described by historians as the worst year to be alive",
    "The Carrington Event (1859), the solar storm that set telegraph offices on fire",
    "The disappearance of the Ninth Roman Legion and the competing explanations",
    "D.B. Cooper, the only unsolved airplane hijacking in American history",
    "The Somerton Man, the unidentified body found with a torn page from a Persian poem",
    "The Max Headroom broadcast intrusion (1987), the hijacked television signal never traced",
    "The Isdal Woman, found in a Norwegian valley with every label removed from her clothing",
    "Kaspar Hauser, the boy who appeared in Nuremberg in 1828 claiming he had grown up in a dark cell",
    "The Amber Room, the chamber looted in 1941 and never recovered",
    "The Library of Alexandria and what was actually lost, against the myth",
    "Genghis Khan's hidden tomb and why Mongolia has not looked for it",
    "The Phaistos Disc, the Bronze Age artifact stamped with symbols used nowhere else",
    "The Eilean Mor lighthouse keepers, three men gone from a locked island station in 1900",
    "Agatha Christie's eleven-day disappearance in 1926, which she never explained",
    "The Hinterkaifeck murders (1922), the farm where the killer appears to have lived in the attic before acting",
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
ALL_TOPICS = CORE_TOPICS + EXPERIMENT_TOPICS + PUBLISHED_TOPICS


def _topic_for_slot(slot: int) -> str:
    """The pool entry a given rotation slot maps to."""
    if EXPERIMENT_EVERY and slot % EXPERIMENT_EVERY == 0 and EXPERIMENT_TOPICS:
        return EXPERIMENT_TOPICS[(slot // EXPERIMENT_EVERY) % len(EXPERIMENT_TOPICS)]

    # Subtract the slots already spent on experiments so the core pool advances
    # by exactly one each time it is used, instead of skipping every 7th entry.
    core_slot = slot - (slot // EXPERIMENT_EVERY if EXPERIMENT_EVERY else 0)
    return CORE_TOPICS[core_slot % len(CORE_TOPICS)]


def pick_topic(offset: int = 0, exclude=()) -> str:
    """The topic for today's slot, skipping anything already published.

    The date rotation alone is not memory. Two runs on the same day — a manual
    trigger and the scheduled one — land on the same slot and therefore the same
    subject, which is exactly how the channel ended up with four Light Brigade
    Shorts, two Ariane 5s and two Great Fires. `exclude` is what published_history
    reads back off the channel, and it is what makes a re-run safe.

    Returns None when every topic in the pool has already been published. That is
    deliberate: publishing nothing is better than publishing a duplicate, and the
    caller halts with a message asking for new topics.
    """
    exclude = {e.strip() for e in exclude if isinstance(e, str) and e.strip()}
    slot = datetime.date.today().toordinal() + offset

    for step in range(len(CORE_TOPICS) + len(EXPERIMENT_TOPICS) + 1):
        topic = _topic_for_slot(slot + step)
        if topic not in exclude:
            if step:
                print(f"↪️  Skipped {step} already-published topic(s) "
                      f"in the rotation.")
            return topic

    print("❌ Every topic in the pool has already been published.")
    return None


if __name__ == "__main__":
    # Preview the next three weeks of scheduling.
    for i in range(21):
        day = datetime.date.today() + datetime.timedelta(days=i)
        slot = day.toordinal()
        tag = "EXP " if EXPERIMENT_EVERY and slot % EXPERIMENT_EVERY == 0 else "core"
        print(f"{day} [{tag}] {(pick_topic(i) or '(pool exhausted)')[:88]}")
