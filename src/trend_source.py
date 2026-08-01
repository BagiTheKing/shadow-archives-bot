"""
Trend source — what is actually hot this week.

Two free feeds, no API key, no new dependency:

  Google Trends RSS  — what people are searching right now
  Google News RSS    — what the world section is actually reporting

Trends alone is mostly sport and celebrity noise. World news alone tells you what
happened but not whether anyone is looking for it. Crossing them gives the thing
we want: a subject with genuine search demand behind it.

IMPORTANT — this module chooses a SUBJECT, never a take. Nothing here decides
what the video says about a live event. The trending item is used only to pick
which piece of history to tell, and longform_llm enforces a hard cutoff year on
the result. See pick_trending_subject's docstring.
"""

import datetime
import os
import re
import urllib.request
import xml.etree.ElementTree as ET

TIMEOUT = 25
UA = "Mozilla/5.0 (compatible; ArchiveParadoxBot/1.0)"

TRENDS_URL = "https://trends.google.com/trending/rss?geo={geo}"
NEWS_URL = ("https://news.google.com/rss/headlines/section/topic/{topic}"
            "?hl=en-US&gl={gl}&ceid={gl}:en")

# ---------------------------------------------------------------------------
# Two separate questions, deliberately not conflated:
#
#   WHERE WE LISTEN  — the audience is in the US and Europe, so search interest
#                      is sampled there. A story trending in Lagos that nobody
#                      in Berlin or Chicago is searching for will not earn views
#                      from the people actually watching this channel.
#
#   WHAT WE COVER    — whatever those audiences are actually searching for.
#                      Ranking is by demand, not by geography. An earlier
#                      version of this file rotated a focus region weekly to
#                      force diversity, which was wrong: it would promote, say,
#                      a Nigeria-Cameroon boundary dispute into a slot for
#                      viewers in the US and Europe who are not searching for
#                      it. Diversity comes from the news changing week to week
#                      and from handing the model ten candidates, not from
#                      overriding demand.
#
# TREND_FOCUS_REGION can still pin coverage to a named region when you want it,
# but it is opt-in and off by default.
# ---------------------------------------------------------------------------

# Where search interest is sampled from. Override with TREND_GEOS/TREND_EDITIONS.
AUDIENCE_GEOS = ["US", "GB", "DE", "FR", "ES", "IT", "PL"]
AUDIENCE_EDITIONS = ["US", "GB", "IE"]
# "terms" are matched against candidate text for the region bonus. They must be
# real place names, never the ISO codes: as substrings those are both wrong and
# dangerous — "ng" never appears in "Nigeria" but does in "England", and "ke"
# matches "market".
REGIONS = [
    {"name": "Africa",
     "geos": ["ZA", "NG", "KE", "EG"],
     "editions": ["ZA", "NG", "KE"],
     "terms": ["africa", "nigeria", "kenya", "egypt", "ethiopia", "sudan",
               "congo", "mali", "sahel", "morocco", "algeria", "libya",
               "somalia", "ghana", "cameroon", "zimbabwe", "rwanda", "uganda",
               "senegal", "tanzania", "niger", "chad", "nile"]},
    {"name": "Middle East",
     "geos": ["AE", "SA", "TR", "IL"],
     "editions": ["AE"],
     "terms": ["iran", "iraq", "israel", "saudi", "yemen", "syria", "lebanon",
               "turkey", "gulf", "hormuz", "palestin", "gaza", "qatar",
               "emirates", "kurd", "jordan", "oman", "bahrain", "persian"]},
    {"name": "South Asia",
     "geos": ["IN", "PK", "BD", "LK"],
     "editions": ["IN", "PK"],
     "terms": ["india", "pakistan", "bangladesh", "sri lanka", "kashmir",
               "afghan", "nepal", "bhutan", "maldives", "punjab", "bengal"]},
    {"name": "East Asia and the Pacific",
     "geos": ["JP", "KR", "PH", "ID", "AU"],
     "editions": ["AU", "PH", "SG"],
     "terms": ["china", "chinese", "japan", "korea", "taiwan", "philippines",
               "indonesia", "vietnam", "australia", "pacific", "malaysia",
               "thailand", "myanmar", "burma", "cambodia", "singapore",
               "hong kong", "mongolia"]},
    {"name": "Europe",
     "geos": ["GB", "DE", "FR", "ES", "PL", "UA"],
     "editions": ["GB", "IE"],
     "terms": ["ukraine", "russia", "poland", "germany", "france", "spain",
               "britain", "british", "balkan", "greece", "italy", "serbia",
               "kosovo", "bosnia", "hungary", "romania", "sweden", "norway",
               "finland", "ireland", "portugal", "austria", "crimea", "nato"]},
    {"name": "The Americas",
     "geos": ["US", "CA", "BR", "MX", "AR"],
     "editions": ["US", "CA"],
     "terms": ["united states", "american", "mexico", "brazil", "venezuela",
               "colombia", "argentina", "cuba", "haiti", "peru", "chile",
               "canada", "bolivia", "panama", "nicaragua", "caribbean"]},
]


REGIONS_BY_NAME = {r["name"].lower(): r for r in REGIONS}


def focus_region():
    """Opt-in regional pin via TREND_FOCUS_REGION, e.g. 'Middle East'.
    Returns None by default, meaning rank purely on demand."""
    want = os.getenv("TREND_FOCUS_REGION", "").strip().lower()
    if not want:
        return None
    region = REGIONS_BY_NAME.get(want)
    if not region:
        print(f"⚠️ Unknown TREND_FOCUS_REGION {want!r}; ignoring. "
              f"Options: {', '.join(r['name'] for r in REGIONS)}")
    return region

# Subjects with a historical spine worth a documentary. Trending terms that do
# not touch any of these are almost always sport, celebrity or product noise.
# Matched as STEMS against the start of each word, so "boundary" catches
# "boundaries" and "disput" catches disputes/disputed/disputing. Exact-word
# matching missed obvious subjects — "Nigeria disputes maritime boundary with
# Cameroon" scored zero because the list had "border" and "disputed" only.
GEO_HINTS = {
    "border", "boundar", "frontier", "territor", "sovereign", "enclave",
    "treat", "ceasefire", "armistice", "conflict", "war", "militar", "troop",
    "army", "navy", "naval", "maritime", "airstrike", "strike", "invas",
    "occupa", "annex", "referend", "independen", "colon", "empire", "imperial",
    "protest", "coup", "junta", "election", "sanction", "embargo", "blockad",
    "migra", "refugee", "strait", "canal", "island", "disput", "claim",
    "summit", "alliance", "nato", "parliament", "government", "president",
    "minister", "monarch", "dam", "pipeline", "oil", "gas", "nuclear",
    "missile", "secess", "partition", "insurgen", "rebel", "uprising",
    "revolution", "siege", "massacre", "exile", "hostage", "espionage",
}

# Categories to keep out of an automated history channel entirely.
EXCLUDE = {
    "nfl", "nba", "mlb", "nhl", "ufc", "premier league", "la liga", "fifa",
    "super bowl", "world cup", "olympics", "grammy", "oscars", "netflix",
    "spotify", "taylor swift", "box office", "trailer", "episode", "season",
    "stock", "crypto", "bitcoin", "earnings", "iphone", "android", "sale",
    "weather", "forecast", "lottery", "recipe", "vs", "score", "highlights",
}


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read()


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _items(xml_bytes):
    """Yields {tag: text} per <item>, ignoring XML namespaces."""
    root = ET.fromstring(xml_bytes)
    for item in root.iter():
        if _local(item.tag) != "item":
            continue
        out = {}
        for child in item:
            name = _local(child.tag)
            if child.text and name not in out:
                out[name] = child.text.strip()
        if out:
            yield out


def fetch_google_trends(geos=("US", "GB")):
    """Trending search terms. Returns [] rather than raising — a dead feed must
    degrade to the normal topic rotation, not fail the run."""
    terms = []
    for geo in geos:
        try:
            for it in _items(_get(TRENDS_URL.format(geo=geo))):
                title = it.get("title", "").strip()
                if title:
                    terms.append(title)
        except Exception as e:
            print(f"⚠️ Google Trends ({geo}) unavailable: {e}")
    return terms


def fetch_headlines(editions=("US",), topics=("WORLD",)):
    """Recent headlines from Google News, across regional editions. Editions
    matter: the US WORLD feed and the Nigerian WORLD feed lead with different
    stories, which is most of how this lane avoids covering one conflict forever."""
    heads = []
    for gl in editions:
        for topic in topics:
            try:
                for it in _items(_get(NEWS_URL.format(topic=topic, gl=gl))):
                    title = it.get("title", "").strip()
                    if title:
                        # Google News appends " - Publisher"; drop it.
                        heads.append(re.sub(r"\s+-\s+[^-]+$", "", title))
            except Exception as e:
                print(f"⚠️ Google News ({gl}/{topic}) unavailable: {e}")
    return heads


def _excluded(text):
    low = text.lower()
    return any(bad in low for bad in EXCLUDE)


# Short stems must match whole words. Prefix-matching them is actively wrong:
# "war" hits warren/warehouse/warning, "dam" hits damage, "coup" hits couple.
# A live run ranked the trending name "mark warren" as geopolitical this way.
_SHORT_EXACT = {s for s in GEO_HINTS if len(s) <= 4}
_PREFIX = {s for s in GEO_HINTS if len(s) > 4}


def _geopolitical(text):
    """Long stems match by prefix so plurals survive; short ones match whole
    words only, plus a bare plural."""
    words = re.findall(r"[a-z]+", text.lower())
    for w in words:
        if w in _SHORT_EXACT or (w.endswith("s") and w[:-1] in _SHORT_EXACT):
            return True
        if any(w.startswith(stem) for stem in _PREFIX if len(w) >= len(stem)):
            return True
    return False


def _useful_subject(term: str) -> bool:
    """Rejects trend terms too generic to anchor a documentary.

    Google Trends returns bare nouns like 'airport' or 'flight' that happen to
    appear in a headline and would otherwise score highly. A usable subject
    names something: a place, a state, a person, or an event — so it is either
    multi-word or carries a capital letter.
    """
    t = term.strip()
    if len(t) < 4:
        return False
    words = t.split()
    if len(words) == 1 and t.islower():
        return False
    # A single capitalised word is fine (Ceuta, Gibraltar); a lone generic is not.
    return any(c.isupper() for c in t) or len(words) >= 2


def rank_candidates(trends, headlines, limit=12, region=None):
    """Ranks subjects by (searched for) x (actually in the news).

    A trending term that also appears in a world headline scores highest: people
    are looking for it AND there is a real event behind it. Headlines that are
    clearly geopolitical come next, so the lane still works on a quiet week when
    Trends is all football.

    Scoring is demand-first, hottest subject wins:

        5  a trending search term that also appears in the news
        3  a trending search term with a geopolitical angle
        2  a headline containing something people are searching for
        1  a plain geopolitical headline (the quiet-week floor)

    Trend-backed items therefore always outrank bare headlines, which is what
    keeps the lane on subjects the audience is actually looking for rather than
    on whatever the wire happened to file.

    region is an optional pin (TREND_FOCUS_REGION); when unset, geography plays
    no part at all.
    """
    # Only geopolitical headlines count as corroboration. A trending actor's
    # name appearing in an entertainment headline is not evidence of demand for
    # a history documentary.
    geo_heads = [h for h in headlines if _geopolitical(h) and not _excluded(h)]
    joined = " || ".join(geo_heads).lower()
    region_terms = [t.lower() for t in (region or {}).get("terms", [])]
    scored = []

    def bonus(text):
        low = text.lower()
        return 2 if region_terms and any(t in low for t in region_terms) else 0

    hot = []
    for term in trends:
        if _excluded(term) or not _useful_subject(term):
            continue
        in_news = term.lower() in joined
        geo = _geopolitical(term)
        if not (in_news or geo):
            continue
        hot.append(term.lower())
        scored.append(((5 if in_news else 3) + bonus(term), term, "trend"))

    for h in headlines:
        if _excluded(h) or not _geopolitical(h):
            continue
        # A headline naming something people are searching for beats one nobody
        # has looked up.
        low = h.lower()
        demand = any(t in low for t in hot)
        scored.append(((2 if demand else 1) + bonus(h), h, "headline"))

    seen, out = set(), []
    for score, text, kind in sorted(scored, key=lambda x: -x[0]):
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append({"subject": text, "score": score, "source": kind})
        if len(out) >= limit:
            break
    return out


def get_trending_subjects(limit=12, region=None):
    """Entry point. Returns (region_name, candidates); candidates is [] on total
    failure so the caller can fall back to the normal topic rotation.

    TREND_GEOS / TREND_EDITIONS override the weekly rotation when set, which is
    what workflow_dispatch uses to force a specific part of the world.
    """
    region = region if region is not None else focus_region()
    geos = [g.strip() for g in os.getenv("TREND_GEOS", "").split(",") if g.strip()] \
        or AUDIENCE_GEOS
    editions = [e.strip() for e in os.getenv("TREND_EDITIONS", "").split(",") if e.strip()] \
        or AUDIENCE_EDITIONS
    topics = [t.strip() for t in os.getenv("TREND_TOPICS", "WORLD").split(",") if t.strip()]

    print(f"🎧 Sampling interest in {geos} / news {editions}")
    if region:
        print(f"📍 Pinned to {region['name']} (TREND_FOCUS_REGION)")

    trends = fetch_google_trends(geos)
    heads = fetch_headlines(editions, topics)
    print(f"📈 {len(trends)} trending terms; {len(heads)} headlines")

    ranked = rank_candidates(trends, heads, limit=limit, region=region)
    for r in ranked[:8]:
        print(f"   [{r['score']}] {r['source']:8} {r['subject'][:78]}")
    if not ranked:
        print("⚠️ No usable trending subject found.")
    label = region["name"] if region else "global demand"
    return label, ranked


if __name__ == "__main__":
    label, rows = get_trending_subjects()
    print(f"\n--- {label} ---")
    for r in rows:
        print(r)
