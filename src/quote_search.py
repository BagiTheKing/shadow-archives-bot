"""
Quote search — real, citable quotes for the cold-open card.

Wikiquote, not an LLM's memory: a free model asked to "recall" a historical
quote will confidently invent a plausible-sounding fake and misattribute it
to a real person, and this pipeline publishes without anyone reading the
script first. Every candidate here comes from an actual Wikiquote page —
human-curated, sourced entries — so the model's job (see longform_llm's
_select_real_quote) is only to PICK and mark one verbatim, never to write
the words itself. No API key, no new dependency: same urllib.request
pattern trend_source.py already uses for a free, keyless feed.

If nothing suitable turns up, search_quotes returns [] and the caller
falls back to an original, unattributed line — see
longform_llm._source_intro_quote.
"""

import json
import re
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT = 20
UA = "Mozilla/5.0 (compatible; ArchiveParadoxBot/1.0)"
API = "https://en.wikiquote.org/w/api.php"

MAX_PAGES = 3
MAX_CANDIDATES = 12
MIN_QUOTE_WORDS = 6
MAX_QUOTE_WORDS = 40


def _get(params: dict) -> dict:
    url = API + "?" + urllib.parse.urlencode(params)
    from archive_engine import wiki_auth, wiki_token_refused
    req = urllib.request.Request(url, headers=wiki_auth(url, {"User-Agent": UA}))
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        if not wiki_token_refused(e.code):
            raise
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read())


def _search_pages(topic: str, limit: int = MAX_PAGES):
    """The Wikiquote pages that best match this topic, best first."""
    data = _get({
        "action": "query", "list": "search", "srsearch": topic,
        "format": "json", "srlimit": limit,
    })
    return [r["title"] for r in data.get("query", {}).get("search", [])]


def _page_wikitext(title: str) -> str:
    data = _get({
        "action": "query", "prop": "revisions", "rvslots": "main",
        "rvprop": "content", "format": "json", "titles": title,
    })
    pages = data.get("query", {}).get("pages", {})
    for _, page_obj in pages.items():
        revs = page_obj.get("revisions") or []
        if not revs:
            continue
        return revs[0].get("slots", {}).get("main", {}).get("*", "") or ""
    return ""


_REF_RE = re.compile(r"<ref[^>]*>.*?</ref>|<ref[^>]*/>", re.DOTALL)
_TEMPLATE_RE = re.compile(r"\{\{[^{}]*\}\}")
_WIKILINK_RE = re.compile(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]")
_EXTLINK_RE = re.compile(r"\[https?://\S+\s+([^\]]+)\]")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_QUOTE_MARKS_RE = re.compile(r"'{2,}")
_LEADING_JUNK_RE = re.compile(r'^[\s"“]+')
_TRAILING_JUNK_RE = re.compile(r'[\s"”]+$')

# Lines that are structurally a bullet but are never a quote to use as-is:
# a citation/date-only line, a "see also" pointer, or a section note.
_SKIP_PREFIXES = (
    "see also", "quotes about", "external link", "source:", "http",
)

# A nested attribution line ("**Valery Legasov, in a taped memoir (1988)")
# often trails off into a citation after the actual name — cut there.
_ATTRIBUTION_CUTOFFS = re.compile(
    r",?\s*(?:as (?:quoted|cited|reported)|in (?:a|an|his|her|the)\b|"
    r"quoted in|cited in)\b.*$|\(.*$", re.IGNORECASE)

# A person's own Wikiquote page is almost always named after them — a
# handful of capitalised words, no digits. A topic/event page reads
# nothing like that, and never gets used as a fallback "who said this".
_EVENT_WORDS = {
    "disaster", "war", "accident", "crisis", "collapse", "battle", "attack",
    "explosion", "incident", "plague", "famine", "revolution", "massacre",
    "uprising", "siege", "invasion", "bombing", "crash", "flood",
    "earthquake", "pandemic", "epidemic", "coup", "genocide", "riot",
    "outbreak", "wreck", "meltdown", "sinking", "shooting", "strike",
}


def _clean_wikitext(s: str) -> str:
    s = _REF_RE.sub("", s)
    s = _TEMPLATE_RE.sub("", s)
    s = _EXTLINK_RE.sub(r"\1", s)
    s = _WIKILINK_RE.sub(r"\1", s)
    s = _HTML_TAG_RE.sub("", s)
    s = _QUOTE_MARKS_RE.sub("", s)
    s = _LEADING_JUNK_RE.sub("", s)
    s = _TRAILING_JUNK_RE.sub("", s)
    return re.sub(r"\s+", " ", s).strip()


def _looks_like_person(title: str) -> bool:
    """Whether a Wikiquote page title looks like a person's own quotes
    page (so its bullets — which carry no per-quote attribution because
    the page's subject already IS the speaker — can safely default to
    it), as opposed to a topic/event page that aggregates quotes from
    many different real people under per-quote attributions."""
    low = title.lower()
    if any(w in low for w in _EVENT_WORDS):
        return False
    words = title.split()
    if not (1 <= len(words) <= 4) or any(ch.isdigit() for ch in title):
        return False
    return all(w[:1].isupper() for w in words if w)


def _clean_attribution(s: str):
    s = _clean_wikitext(s)
    s = _ATTRIBUTION_CUTOFFS.sub("", s).strip(" ,;:—-")
    return s or None


def _extract_quotes(wikitext: str, page_title: str):
    """Top-level bullet lines — Wikiquote's own convention for a quote
    entry — that read like an actual quoted sentence rather than a
    citation or a section note. A nested "**" line right after a quote is
    that quote's real, sourced speaker on a topic page aggregating many
    people's words; only when there is no such line, and the page itself
    looks like a person's own page (see _looks_like_person), does the
    page title become the attribution — a topic page's quote with no
    per-line attribution is dropped rather than pinned on the topic
    itself, which is never who actually said it.

    Returns [{"text": ..., "attribution": ...}, ...].
    """
    lines = wikitext.splitlines()
    default_attribution = page_title if _looks_like_person(page_title) else None

    out = []
    for i, line in enumerate(lines):
        if not line.startswith("*") or line.startswith("**"):
            continue
        text = _clean_wikitext(line[1:])
        if not text or text.lower().startswith(_SKIP_PREFIXES):
            continue
        n = len(text.split())
        if not (MIN_QUOTE_WORDS <= n <= MAX_QUOTE_WORDS):
            continue

        attribution = None
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if nxt.startswith("**") and not nxt.startswith("***"):
            attribution = _clean_attribution(nxt[2:])
        if not attribution:
            attribution = default_attribution
        if not attribution:
            continue
        out.append({"text": text, "attribution": attribution})
    return out


def search_quotes(topic: str) -> list:
    """Real, sourced quote candidates for this topic:
    [{"text": ..., "attribution": ..., "source_page": ...}, ...],
    best-effort. Returns [] if Wikiquote has nothing usable, or nothing
    with a safely-identifiable real speaker, or could not be reached —
    never raises, since a lookup failure should cost the card its real
    quote, not the run.
    """
    try:
        pages = _search_pages(topic)
    except Exception as e:
        print(f"⚠️ Wikiquote search failed ({type(e).__name__}: {e}).")
        return []

    candidates = []
    for page in pages:
        try:
            wikitext = _page_wikitext(page)
        except Exception as e:
            print(f"⚠️ Wikiquote page fetch failed for {page!r} "
                  f"({type(e).__name__}: {e}).")
            continue
        for q in _extract_quotes(wikitext, page):
            candidates.append({**q, "source_page": page})
        if len(candidates) >= MAX_CANDIDATES:
            break
    return candidates[:MAX_CANDIDATES]
