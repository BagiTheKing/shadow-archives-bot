"""
Per-claim sources for story-mode videos (docs/VIDEO_SPEC.md).

Every fact a story-mode video states comes from one Wikipedia article (the
fact sheet is verified against it), and every sentence of that article
carries footnotes naming the book, paper or page it rests on. This module
reads the article's rendered HTML once, keeps each prose sentence with its
footnotes, and then, for each narration line, finds the article sentence it
was written from and returns that sentence's source.

No model is involved anywhere: a narration line that does not clearly match
one cited sentence gets no source rather than a guessed one.
"""

import html as _html
import re
from html.parser import HTMLParser

WIKI_API = "https://en.wikipedia.org/w/api.php"

LABEL_MAX_CHARS = 64      # the on-screen tag, bottom right
FULL_MAX_CHARS = 220      # one line in the description


def _clean(text):
    return re.sub(r"\s+", " ", _html.unescape(text or "")).strip()


class _Parser(HTMLParser):
    """Collects prose (<p>) text with the footnote ids that follow each piece,
    the footnote texts (<li id="cite_note-...">), and the full bibliography
    entries (<cite id="CITEREF...">)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.pieces = []            # [(text, note_id or None)] in page order
        self.notes = {}             # note id -> {"text", "refs": [CITEREF ids], "title"}
        self.cites = {}             # CITEREF id -> {"text", "title"}
        self._p = 0
        self._skip = 0              # inside <style>, <sup> etc.
        self._sup_note = None
        self._note = None
        self._note_depth = 0
        self._cite = None
        self._i = 0
        self._buf = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get("class") or ""
        if tag in ("style", "script", "table"):
            self._skip += 1
            return
        if tag == "p" and not self._note:
            self._p += 1
        if tag == "sup":
            self._skip += 1
            if "reference" in cls:
                self._sup_note = ""
        if tag == "a" and self._sup_note == "" and (a.get("href") or "").startswith("#cite_note-"):
            self._sup_note = a["href"][1:]
        if tag == "li" and (a.get("id") or "").startswith("cite_note-"):
            self._note = {"id": a["id"], "text": [], "refs": [], "title": []}
            self._note_depth = 1
        elif tag == "li" and self._note:
            self._note_depth += 1
        if self._note is not None:
            href = a.get("href") or ""
            if tag == "a" and href.startswith("#CITEREF"):
                self._note["refs"].append(href[1:])
            if "mw-cite-backlink" in cls:
                self._skip += 1
                self._note["_back"] = True
        if tag == "cite" and (a.get("id") or "").startswith("CITEREF"):
            self._cite = {"id": a["id"], "text": [], "title": []}
        if tag == "i":
            self._i += 1

    def handle_endtag(self, tag):
        if tag in ("style", "script", "table"):
            self._skip = max(0, self._skip - 1)
            return
        if tag == "p" and self._p:
            self._p -= 1
        if tag == "sup":
            self._skip = max(0, self._skip - 1)
            if self._sup_note and self._p:
                self.pieces.append(("", self._sup_note))
            self._sup_note = None
        if tag == "span" and self._note is not None and self._note.pop("_back", None):
            self._skip = max(0, self._skip - 1)
        if tag == "li" and self._note is not None:
            self._note_depth -= 1
            if self._note_depth <= 0:
                n = self._note
                self.notes[n["id"]] = {"text": _clean("".join(n["text"])),
                                       "refs": n["refs"],
                                       "title": _clean("".join(n["title"]))}
                self._note = None
        if tag == "cite" and self._cite is not None:
            c = self._cite
            self.cites[c["id"]] = {"text": _clean("".join(c["text"])),
                                   "title": _clean("".join(c["title"]))}
            self._cite = None
        if tag == "i":
            self._i = max(0, self._i - 1)

    def handle_data(self, data):
        if self._cite is not None:
            self._cite["text"].append(data)
            if self._i:
                self._cite["title"].append(data)
        if self._note is not None:
            if not self._skip:
                self._note["text"].append(data)
                if self._i:
                    self._note["title"].append(data)
            return
        if self._p and not self._skip:
            self.pieces.append((data, None))


def _sentences(pieces):
    """[(sentence, [note ids])]: footnotes attach to the sentence they
    follow, whether they sit mid-sentence or right after its full stop."""
    out, cur, notes = [], [], []
    for text, note in pieces:
        if note:
            if cur and "".join(cur).strip():
                notes.append(note)
            elif out:
                out[-1][1].append(note)
            continue
        parts = re.split(r"(?<=[.!?])\s+(?=[A-Z\"'(])", text)
        for k, part in enumerate(parts):
            cur.append(part)
            if k < len(parts) - 1 or re.search(r"[.!?][\"')]?\s*$", part):
                s = _clean("".join(cur))
                if s:
                    out.append((s, notes))
                cur, notes = [], []
    s = _clean("".join(cur))
    if s:
        out.append((s, notes))
    return [(s, n) for s, n in out if len(s.split()) >= 4]


_YEAR = re.compile(r"\((?:[^()]*?\s)?(\d{4})[a-z]?\)|\b(1[5-9]\d\d|20\d\d)\b")
_TAIL = re.compile(r"(?:^|\s+|\.\s*)(Archived from|Archived|Retrieved|ISBN|ISSN|OCLC|"
                   r"doi:|S2CID|JSTOR|PMID|Bibcode|Accessed|pp?\.\s).*$")


def _short(text, limit):
    text = _clean(text)
    return text if len(text) <= limit else text[:limit - 1].rstrip(" ,.;:") + "…"


def _author(head):
    """The first author's surname from the start of an entry ("Sachar,
    Howard M. (2007)", "Sachar (2007)", "Major General Indar Jit Rikhye
    (2013)"), or "" when the entry starts with its title in quotes."""
    head = head.strip()
    if not head or head[0] in "\"'\u201c":
        return ""
    head = re.split(r"\s*\(|\.\s|\s&\s|\sand\s", head, maxsplit=1)[0]
    if "," in head:
        return head.split(",")[0].strip()
    # "Israel Ministry of Foreign Affairs" stays whole; a long run of words
    # is a title, not an author
    return head if 0 < len(head) <= 34 and len(head.split()) <= 6 else ""


def _source_of(note, cites):
    """{"label", "full"} for one footnote, or None if it is only a remark."""
    text, refs = note["text"], note["refs"]
    cite = next((cites[r] for r in refs if r in cites), None)
    entry = cite["text"] if cite else text
    pub = _TAIL.sub("", cite["title"] if cite else note["title"]).strip(" .\"")
    # the short title on screen: "Six Days of War", not its subtitle
    pub = re.split(r":\s|\s\(", pub)[0].strip(" .")
    full = _TAIL.sub("", entry).strip(" .")
    full = re.sub(r"^\d+\s+", "", full)
    if not full or len(full) < 6:
        return None
    quoted = (re.search(r"[\"\u201c]([^\"\u201d]{4,})[\"\u201d]", full) or [None, ""])[1].strip(" .")
    author = _author(text if cite else full)
    m = _YEAR.search(full)
    year = (m.group(1) or m.group(2)) if m else ""
    if author and pub:
        label = f"{author}, {pub}"          # a book, or an article's journal
    elif author and quoted:
        label = f"{author}, {quoted}"
    elif quoted and pub:
        label = f"{pub}: {quoted}"          # a web page with no author
    else:
        label = pub or quoted or author or full
    if len(re.sub(r"\W", "", label)) < 8:
        label = full                        # too little to name anything
    if year and year not in label:
        label = f"{label} ({year})"
    return {"label": _short(label, LABEL_MAX_CHARS), "full": _short(full, FULL_MAX_CHARS)}


def parse(page_html, title="", url=""):
    """The article's cited sentences: {"title", "url", "sentences": [(text,
    source)]} where source is {"label", "full"}; sentences with no usable
    footnote are left out."""
    p = _Parser()
    p.feed(page_html or "")
    sources = {}
    for nid, note in p.notes.items():
        src = _source_of(note, p.cites)
        if src:
            sources[nid] = src
    out = []
    for text, notes in _sentences(p.pieces):
        src = next((sources[n] for n in notes if n in sources), None)
        if src:
            out.append((text, src))
    return {"title": title, "url": url, "sentences": out}


def fetch(title, url=""):
    """parse() for a Wikipedia article title, or None on any failure."""
    try:
        from archive_engine import _polite_get
        r = _polite_get(WIKI_API, params={
            "action": "parse", "page": title, "prop": "text", "redirects": 1,
            "format": "json", "formatversion": 2}, timeout=60)
        page = ((r.json() or {}).get("parse") or {}).get("text") or ""
        if not page:
            return None
        got = parse(page, title, url)
        print(f"📎 Claim sources: {len(got['sentences'])} cited sentences in {title!r}.")
        return got
    except Exception as e:
        print(f"⚠️ Claim sources unavailable ({type(e).__name__}: {e}).")
        return None


# ---------------------------------------------------------------------------
# Matching a narration line to the sentence it came from
# ---------------------------------------------------------------------------

_STOP = set("""the a an and or but of in on at to for from with by as that this
these those was were is are be been had has have it its their they them he
she his her who which what when where while after before into over under
than then there here also would could only more most some many such not no
said says about against between during within without upon onto""".split())

_NUMBER_WORDS = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
    "seven": "7", "eight": "8", "nine": "9", "ten": "10", "eleven": "11",
    "twelve": "12", "first": "1", "second": "2", "third": "3", "fourth": "4",
    "fifth": "5", "sixth": "6", "seventh": "7", "eighth": "8", "ninth": "9",
    "tenth": "10", "hundred": "100", "thousand": "1000"}


_MONTH_NAMES = {"january", "february", "march", "april", "may", "june", "july",
                "august", "september", "october", "november", "december",
                "monday", "tuesday", "wednesday", "thursday", "friday",
                "saturday", "sunday"}


def _keys(text):
    """(names, words, subject): capitalised names, keyed on their first five
    letters so "Israeli" meets "Israel" and "Syrian" meets "Syria"; every
    content word (names included), plural and possessive endings dropped,
    spoken small numbers as digits; and the first name in the text, which
    is usually who the sentence is about."""
    names, words, subject = set(), set(), None
    toks = re.findall(r"[A-Za-z][A-Za-z'-]+|\d[\d,]*", text or "")
    for t in toks:
        low = re.sub(r"'s$", "", t.lower()).strip("'")
        if t[0].isdigit():
            words.add(t.replace(",", ""))
            continue
        if low in _NUMBER_WORDS:
            words.add(_NUMBER_WORDS[low])
            continue
        m = re.fullmatch(r"(twenty|thirty)-(\w+)", low)
        if m and m.group(2) in _NUMBER_WORDS:
            words.add(str((20 if m.group(1) == "twenty" else 30)
                          + int(_NUMBER_WORDS[m.group(2)])))
            continue
        if low in _STOP or len(low) < 3:
            continue
        if t[0].isupper() and low not in _MONTH_NAMES:
            key = low[:5]
            names.add(key)
            words.add(key)
            subject = subject or key
            continue
        words.add(low[:-1] if len(low) > 4 and low.endswith("s") else low)
    return names, words, subject


def match(line, index, min_score=7.0, with_text=False):
    """The source of the article sentence `line` was written from, or None.

    Shared words are weighted by how rare they are in the article (a word
    like "Israel" in the Six-Day War article proves nothing; "Tiran" or
    "pact" does). A candidate sentence must also name who the line is about
    (its first name), miss at most one of the line's names, and share two
    words that are not names (one, with two shared names). Tested on real
    articles: "Nasser closed the Straits" must not match "Dayan announced
    the Straits open", and it does not. No match is better than a wrong one.
    """
    if not index or not line:
        return None
    sents, idf = index
    ln, lw, subject = _keys(line)
    if len(lw) < 3:
        return None
    total = sum(idf.get(w, 3.0) for w in lw)
    best, best_score = None, 0.0
    for text, src, (sn, sw, _) in sents:
        shared = lw & sw
        other = shared - ln
        if subject:
            if subject not in sn or len(ln - sn) > 1:
                continue
            if len(other) < 2 and not (other and len(ln & sn) >= 2):
                continue
        elif len(other) < 3 or sum(idf.get(w, 1.0) for w in other) < 10:
            # a line that names no one ("On the ninth of October, a
            # landslide fell into the lake"): only a strong match counts
            continue
        score = sum(idf.get(w, 1.0) for w in shared)
        # and it must carry at least half of what the line says, weighted
        # by rarity ("reactor" and "1986" alone do not make "reactor four
        # exploded during a safety test")
        if score < 0.5 * total:
            continue
        if score >= min_score and score > best_score:
            best, best_score = (src, text), score
    if with_text:
        return best
    return best[0] if best else None


def build_index(parsed):
    """Precomputed keys and word rarity for match()."""
    import math
    sents = [(t, s, _keys(t)) for t, s in (parsed or {}).get("sentences") or []]
    df = {}
    for _, _, (_, words, _) in sents:
        for w in words:
            df[w] = df.get(w, 0) + 1
    n = max(1, len(sents))
    idf = {w: math.log((n + 1) / (c + 0.5)) for w, c in df.items()}
    return sents, idf


def attach(scenes, parsed, sheet=None):
    """Sets scene["claim_source"] = {"label", "full"} on every scene whose
    narration matches a cited sentence; returns the distinct sources used,
    in order of first appearance (for the description)."""
    index = build_index(parsed)
    used, seen = [], set()
    title = (parsed or {}).get("title") or ""
    article = ({"label": f"Wikipedia: {title}", "full": f"Wikipedia, {title}"}
               if title else None)
    facts = _fact_keys(sheet)
    if not index[0] and not article:
        return used
    for s in scenes:
        best = None
        for sent in re.split(r"(?<=[.!?])\s+", s.get("narration") or ""):
            got = match(sent, index)
            if got:
                best = got
                break
        if not best and article and _states_fact(s.get("narration") or "", facts):
            # no footnote matched clearly, but the line states a fact from
            # the fact sheet, and every fact sheet item is checked against
            # this article: the article is its source (2026-10-01: only 3 of
            # 67 scenes matched a footnote, so almost nothing showed)
            best = article
        if best:
            s["claim_source"] = best
            if best["full"] not in seen and best is not article:
                seen.add(best["full"])
                used.append(best)
    return used


_MONTHS = ("january february march april may june july august september "
           "october november december").split()


def _fact_keys(sheet):
    """Names and years the fact sheet states."""
    keys = set()
    for t in (sheet or {}).get("timeline") or []:
        keys.update(re.findall(r"\b(1\d{3}|20\d{2})\b", str(t.get("date") or "")))
        for p in t.get("people") or []:
            w = [x for x in re.findall(r"[A-Za-z]+", p) if len(x) > 3]
            if w:
                keys.add(w[-1].lower())
    for n in (sheet or {}).get("numbers") or []:
        for v in re.findall(r"\d[\d,]*", str(n.get("value") or "")):
            keys.update({v, v.replace(",", "")})
    return keys


def _states_fact(line, facts):
    """A line that states something checkable: a year, a date, a figure
    from the sheet, or a sheet person together with a date or a number."""
    low = line.lower()
    has_date = bool(re.search(r"\b(1\d{3}|20\d{2})\b", line)) or any(m in low for m in _MONTHS)
    has_num = bool(re.search(r"\d", line)) or bool(re.search(
        r"\b(hundred|thousand|million)s?\b", low))
    names = any(k in low for k in facts if k.isalpha())
    figure = any(n.replace(",", "") in facts or n in facts
                 for n in re.findall(r"\d[\d,]*", line))
    return has_date or figure or (names and has_num)
