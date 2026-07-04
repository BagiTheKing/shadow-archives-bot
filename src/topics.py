"""
Curated topic pool for The Shadow Archives.

Why this exists: letting the LLM pick its own topic every day produces the same
5 famous stories over and over (Roman legion vanishing, Dancing Plague, etc.),
which makes the channel feel mass-produced. A curated pool + date-based rotation
guarantees ~2 months of unique, verifiably real stories before any repeat.

Add your own topics to the list — the more specific, the better the script.
"""

import datetime

TOPICS = [
    # Military blunders & strategic disasters
    "The Battle of Karansebes (1788), where the Austrian army attacked itself and suffered hundreds of casualties without meeting the enemy",
    "Operation Cottage (1943), where 35 Allied soldiers died invading Kiska island that the Japanese had already abandoned",
    "The Charge of the Light Brigade (1854) and the ambiguous order that sent cavalry into a valley of cannons",
    "The Maginot Line and how Germany simply went around France's 'impenetrable' defenses in 1940",
    "The Battle of New Orleans (1815), fought two weeks after the war had officially ended",
    "Spain's Anjouan expedition mishaps and other invasions launched on wrong intelligence",
    "The Darien Scheme (1698), Scotland's colonial gamble that bankrupted the nation and led to union with England",
    "Operation Eiche and the glider rescue of Mussolini that succeeded despite absurd risks",
    "The Siege of Petersburg crater disaster (1864), where Union troops charged into the hole they had just blown",
    "The sinking of the Vasa (1628), Sweden's mightiest warship that sank 20 minutes into its maiden voyage",
    "The Halifax Explosion (1917), the largest man-made blast before the atomic bomb, caused by a slow-motion harbor collision everyone watched",
    "Operation Mincemeat (1943), the corpse with fake documents that fooled Hitler",
    "The Ghost Army of WWII, the American unit of artists that fooled Germany with inflatable tanks",
    "The Emu War (1932), when Australia deployed machine guns against birds and lost",
    "The Battle of Los Angeles (1942), when the US fired 1,400 shells at nothing",
    "The Millennium Bridge wobble and other engineering marvels that failed on day one",
    "Napoleon's invasion of Russia and the mathematics of how 400,000 soldiers became 10,000",
    "The Zimmermann Telegram, the intercepted message that pulled America into WWI",
    "The Trojan Horse of Meknes and other lesser-known deception tactics in siege warfare",
    "The Christmas Truce of 1914, when enemies played football between the trenches",

    # Historical paradoxes & eerie coincidences
    "The Jim Twins, separated at birth, who lived nearly identical lives without knowing each other",
    "The novel 'Futility' (1898) that described the Titanic disaster 14 years before it happened",
    "Tsutomu Yamaguchi, the man who survived both Hiroshima and Nagasaki",
    "The curious case of the two Dennis the Menace comics debuting the same week on opposite sides of the Atlantic",
    "Lincoln and Kennedy assassination parallels, and which ones are actually true",
    "The Dancing Plague of 1518, when hundreds of people danced themselves to exhaustion in Strasbourg",
    "The Dyatlov Pass incident (1959) and the leading scientific explanation for the hikers' deaths",
    "The Antikythera Mechanism, the 2,000-year-old computer found in a shipwreck",
    "Roman dodecahedrons, the ancient objects found across Europe that no one can explain",
    "The Voynich Manuscript, the book written in a language no one has ever decoded",
    "The Wow! Signal (1977), the 72-second radio burst from deep space never heard again",
    "The Bloop, the ultra-low frequency sound from the ocean that was louder than a blue whale",
    "The Mary Celeste (1872), the ship found sailing perfectly with its entire crew missing",
    "The Lost Colony of Roanoke and the single word 'CROATOAN' carved into a post",
    "Green children of Woolpit, the medieval legend of two green-skinned children who appeared in an English village",
    "The Tunguska Event (1908), the explosion that flattened 80 million trees with no crater",
    "The year 536 AD, described by historians as the worst year to be alive",
    "The Carrington Event (1859), the solar storm that set telegraph offices on fire",
    "The London Beer Flood (1814), when a tsunami of beer killed eight people",
    "The Great Molasses Flood of Boston (1919) and the wave of syrup that ran at 35 mph",

    # Vanishings, secrets & strange history
    "The disappearance of the 9th Roman Legion in Britain and the competing theories",
    "D.B. Cooper, the only unsolved airplane hijacking in American history",
    "The Somerton Man, the unidentified body on an Australian beach with a torn page from a Persian poem",
    "The Max Headroom broadcast intrusion (1987), the hijacked TV signal never traced",
    "The Circleville Letters, the poison-pen mystery that outlived its only suspect",
    "The Isdal Woman, found in a Norwegian valley with all labels removed from her clothes",
    "Kaspar Hauser, the boy who appeared in Nuremberg in 1828 claiming he grew up in a dark cell",
    "The Princes in the Tower and the 500-year-old royal murder mystery",
    "The Amber Room, the priceless chamber looted by Nazis and never found",
    "The Library of Alexandria and what was actually lost, versus the myth",
    "Genghis Khan's hidden tomb and why Mongolia doesn't want it found",
    "The Oak Island money pit, the 200-year treasure hunt that has consumed fortunes and lives",
    "The Phaistos Disc, the Bronze Age artifact stamped with symbols used nowhere else on Earth",
    "The Bermuda Triangle's Flight 19 and the boring truth the legend ignores",
    "The Hinterkaifeck murders (1922), the German farm where the killer lived in the attic",
    "The Taos Hum and other unexplained sounds heard by only 2% of people",
    "Agatha Christie's own 11-day disappearance in 1926 that she never explained",
    "The Devil's Footprints of 1855, the trail of hoofprints across 100 miles of Devon snow",
    "The Eilean Mor lighthouse keepers, three men who vanished from a locked lighthouse in 1900",
    "The Patomskiy Crater in Siberia, the mound that shouldn't exist",
]


def pick_topic(offset: int = 0) -> str:
    """Deterministically rotates through the pool by date, so every daily run
    gets a different topic and the cycle only repeats after len(TOPICS) days."""
    day_index = (datetime.date.today().toordinal() + offset) % len(TOPICS)
    return TOPICS[day_index]
