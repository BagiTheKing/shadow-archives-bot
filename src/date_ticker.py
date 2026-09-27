"""
Date ticker: time identification for the viewer.

Approved from the Six-Day War preview. Just before the narrator says a date,
a thin time ruler rises from the bottom of the frame, rolls day by day from
the last date the video mentioned to the new one, and lands on it as the
date is spoken: the date flashes red, a red rule draws under it, a time
("07:45") appears beside it when the narration gives one, and a low thud
lands. If the next date comes within a few seconds the bar stays up and
keeps rolling; otherwise it sinks away. Every date continues from the last,
so a flashback after a cold open rolls backwards and a two-week wait takes
longer to roll through than a two-day advance.

Dates come only from the narration as spoken, found in the scene's own word
timings: "the fifth of June", "June 5, 1967", "5 June", "May 1967",
"on the seventh" (the month and year carried from the last date said), and
a year said alone after "in", "by", "since"... ("in 1956"). A time needs
"at" and a clock reading ("at seven forty-five", "at 7:45 a.m."). A year
never said is taken from the last one said, or the story's era.

render_overlays() writes one short transparent clip per stretch the bar is
up; the final pass overlays them at their times. Captions and photo credits
move up while the bar is showing (lift_periods).
"""

import datetime as dt
import math
import os
import re
import shutil
import subprocess
import tempfile

from PIL import Image, ImageDraw, ImageFont

W, H, FPS = 1920, 1080, 30
BAND = 200
BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
ANTON = os.path.join(BASE, "fonts", "Anton-Regular.ttf")
MONO = os.path.join(BASE, "fonts", "FreeMonoBold.ttf")
RED = (205, 58, 46)
INK = (240, 236, 226)
DIM = (160, 156, 148)
MONTHS = ["january", "february", "march", "april", "may", "june", "july",
          "august", "september", "october", "november", "december"]

RISE, LEAD, SCROLL, HOLD, SINK = 0.35, 1.9, 1.4, 1.8, 0.35
MIN_GAP = 1.6            # two dates closer than this: the second is dropped

_ONES = ["", "one", "two", "three", "four", "five", "six", "seven", "eight",
         "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
         "sixteen", "seventeen", "eighteen", "nineteen"]
_ORD = ["", "first", "second", "third", "fourth", "fifth", "sixth", "seventh",
        "eighth", "ninth", "tenth", "eleventh", "twelfth", "thirteenth",
        "fourteenth", "fifteenth", "sixteenth", "seventeenth", "eighteenth",
        "nineteenth"]
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50}
_TENS_ORD = {"twentieth": 20, "thirtieth": 30}
_YEAR_WORDS = {"in", "by", "since", "until", "of", "from", "summer", "spring",
               "autumn", "fall", "winter", "early", "late", "mid"}


def _norm(tok):
    return re.sub(r"[^a-z0-9:'-]", "", (tok or "").lower()).strip("'-")


def _day_at(toks, i):
    """(day, tokens used) for a spoken day at toks[i]: "5", "5th",
    "fifth", "twenty-second", "twenty second". None otherwise."""
    t = toks[i] if i < len(toks) else ""
    m = re.fullmatch(r"(\d{1,2})(st|nd|rd|th)?", t)
    if m and 1 <= int(m.group(1)) <= 31:
        return int(m.group(1)), 1
    if t in _ORD:
        return _ORD.index(t), 1
    if t in _TENS_ORD:
        return _TENS_ORD[t], 1
    if "-" in t:
        a, b = t.split("-", 1)
        if a in _TENS and b in _ORD and _TENS[a] + _ORD.index(b) <= 31:
            return _TENS[a] + _ORD.index(b), 1
    if t in _TENS and i + 1 < len(toks) and toks[i + 1] in _ORD:
        d = _TENS[t] + _ORD.index(toks[i + 1])
        if d <= 31:
            return d, 2
    return None


def _year_at(toks, i):
    t = toks[i] if i < len(toks) else ""
    if re.fullmatch(r"(1[5-9]\d\d|20[0-3]\d)", t):
        return int(t)
    return None


def _number_word(t):
    if t.isdigit():
        return int(t)
    if t in _ONES:
        return _ONES.index(t)
    if t in _TENS:
        return _TENS[t]
    if "-" in t:
        a, b = t.split("-", 1)
        if a in _TENS and b in _ONES:
            return _TENS[a] + _ONES.index(b)
    return None


def _time_near(toks, i0, i1):
    """(clock "07:45", index where it starts) for "at <time>" within a few
    words of a date spanning toks[i0:i1], or (None, None)."""
    for j in range(max(0, i0 - 9), min(len(toks), i1 + 8)):
        if toks[j] != "at":
            continue
        k = j + 1
        t = toks[k] if k < len(toks) else ""
        hh = mm = None
        m = re.fullmatch(r"(\d{1,2}):(\d{2})", t)
        if m:
            hh, mm, k = int(m.group(1)), int(m.group(2)), k + 1
        else:
            h = _number_word(t)
            if h is not None and 1 <= h <= 12:
                hh, k = h, k + 1
                nxt = toks[k] if k < len(toks) else ""
                mins = _number_word(nxt)
                if nxt in ("o'clock", "oclock"):
                    mm, k = 0, k + 1
                elif mins is not None and 1 <= mins <= 59:
                    mm, k = mins, k + 1
                elif nxt in _TENS and k + 1 < len(toks) and toks[k + 1] in _ONES:
                    mm, k = _TENS[nxt] + _ONES.index(toks[k + 1]), k + 2
        if hh is None or mm is None:
            continue
        tail = " ".join(toks[k:k + 5])
        if hh < 12 and re.search(r"\b(pm|p\.m|afternoon|evening|night)\b", tail):
            hh += 12
        return f"{hh:02d}:{mm:02d}", j
    return None, None


def find_dates(words, state):
    """Dates spoken in one scene: [{"at": local seconds, "date", "time",
    "label"}]. state carries the last year and month across scenes."""
    toks = [_norm(w.get("word")) for w in words]
    starts = [float(w.get("start", 0.0)) for w in words]
    out = []
    i = 0
    while i < len(toks):
        found = None
        s = i
        if toks[i] == "the" and _day_at(toks, i + 1):
            s, i1 = i, i + 1
        else:
            i1 = i
        d = _day_at(toks, i1)
        if d:
            day, n = d
            j = i1 + n
            if j < len(toks) and toks[j] == "of" and j + 1 < len(toks) and toks[j + 1] in MONTHS:
                month, j = MONTHS.index(toks[j + 1]) + 1, j + 2
                found = ("day", s, j, day, month)
            elif j < len(toks) and toks[j] in MONTHS:
                month, j = MONTHS.index(toks[j]) + 1, j + 1
                found = ("day", s, j, day, month)
            elif (s >= 1 and toks[s - 1] == "on" and toks[s] == "the"
                  and state.get("month") and (j >= len(toks) or toks[j] not in
                                              ("day", "days", "floor", "time", "century", "of"))):
                found = ("day", s - 1, j, day, None)
        if not found and toks[i] in MONTHS:
            month = MONTHS.index(toks[i]) + 1
            d = _day_at(toks, i + 1)
            if d:
                found = ("day", i, i + 1 + d[1], d[0], month)
            elif _year_at(toks, i + 1):
                found = ("month", i, i + 1, 1, month)
        if not found and _year_at(toks, i) and i >= 1 and toks[i - 1] in _YEAR_WORDS:
            found = ("year", i - 1, i + 1, 1, 7)
        if not found:
            i += 1
            continue
        kind, s, e, day, month = found
        last = None
        year = _year_at(toks, e)
        if year:
            e += 1
        elif kind == "year":
            year = _year_at(toks, e - 1)
        year = year or state.get("year")
        if kind == "day" and month is None:
            month = state["month"]
            last = state.get("ord")
        if not year:
            i = e
            continue
        try:
            date = dt.date(year, month, min(day, 28) if kind != "day" else day)
        except ValueError:
            i = e
            continue
        if kind == "day" and found[4] is None:
            # "on the seventh": only when it moves forward within a month,
            # rolling into the next month when the day has already passed
            # ("22 May ... on the seventh" is 7 June)
            if last and date.toordinal() <= last:
                try:
                    nm = dt.date(year + (month == 12), month % 12 + 1, day)
                    date = nm
                except ValueError:
                    pass
            if not last or not (0 < date.toordinal() - last <= 31):
                i = e
                continue
        clock, tj = _time_near(toks, s, e) if kind == "day" else (None, None)
        at = starts[min(s, tj) if tj is not None else s]
        label = (f"{date.day} {MONTHS[date.month - 1].upper()} {date.year}" if kind == "day"
                 else f"{MONTHS[date.month - 1].upper()} {date.year}" if kind == "month"
                 else f"{date.year}")
        ordv = date.toordinal() + (14 if kind == "month" else 0)
        state.update(year=date.year, month=date.month, ord=ordv)
        out.append({"at": at, "ord": ordv, "label": label, "time": clock or "", "kind": kind})
        i = e
    return out


def collect(scenes_with_offsets, era_year=None):
    """Every date the video speaks, on the video's clock, in order, with
    near-duplicates removed."""
    state = {"year": era_year}
    events = []
    for scene, offset in scenes_with_offsets:
        for ev in find_dates(scene.get("words") or [], state):
            ev["at"] += offset
            if events and (ev["at"] - events[-1]["at"] < MIN_GAP
                           or (ev["ord"] == events[-1]["ord"] and ev["at"] - events[-1]["at"] < 20)):
                continue
            events.append(ev)
    return events


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _ease(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


def _ppd(gap):
    return max(0.02, min(70.0, 760.0 / max(1.0, gap)))


class Ticker:
    def __init__(self, events):
        self.ev = sorted(events, key=lambda e: e["at"])
        for i, e in enumerate(self.ev):
            e["prev"] = self.ev[i - 1]["ord"] if i else e["ord"] - 7
            e["ppd"] = _ppd(abs(e["ord"] - e["prev"]))
        self.up = []
        for e in self.ev:
            a, b = max(0.0, e["at"] - LEAD), e["at"] + HOLD
            if self.up and a <= self.up[-1][1] + RISE + SINK:
                self.up[-1][1] = b
            else:
                self.up.append([a, b])
        self.f_date = ImageFont.truetype(ANTON, 58)
        self.f_mon = ImageFont.truetype(MONO, 22)
        self.f_time = ImageFont.truetype(MONO, 38)

    def periods(self):
        """[(start, end)] the bar is on screen."""
        return [(a, b + SINK) for a, b in self.up]

    def _state(self, t):
        cur, ppd, idx, arrived = None, 20.0, -1, False
        for i, e in enumerate(self.ev):
            s0 = e["at"] - SCROLL
            if t < s0:
                break
            k = _ease((t - s0) / SCROLL)
            cur = e["prev"] + (e["ord"] - e["prev"]) * k
            p0 = self.ev[i - 1]["ppd"] if i else e["ppd"]
            ppd = math.exp(math.log(p0) + (math.log(e["ppd"]) - math.log(p0)) * k)
            idx, arrived = i, t >= e["at"]
        if cur is None and self.ev:
            cur, ppd = self.ev[0]["prev"], self.ev[0]["ppd"]
        return cur, ppd, idx, arrived

    def _lift(self, t):
        for a, b in self.up:
            if a <= t <= b + SINK:
                return _ease((t - a) / RISE) * (1 - _ease((t - b) / SINK))
        return 0.0

    def _rolling_label(self, cur, e):
        d = dt.date.fromordinal(int(round(cur)))
        if e["kind"] == "year":
            return f"{d.year}"
        if e["kind"] == "month":
            return f"{MONTHS[d.month - 1].upper()} {d.year}"
        return f"{d.day} {MONTHS[d.month - 1].upper()} {d.year}"

    def frame(self, t):
        up = self._lift(t)
        if up <= 0.001:
            return None
        cur, ppd, idx, arrived = self._state(t)
        im = Image.new("RGBA", (W, BAND), (0, 0, 0, 0))
        d = ImageDraw.Draw(im, "RGBA")
        strip_top = BAND - 62
        d.rectangle([0, strip_top, W, BAND], fill=(10, 9, 9, 205))
        d.line([(0, strip_top), (W, strip_top)], fill=RED + (140,), width=2)
        base = BAND - 16
        cx = W / 2
        if ppd >= 1.0:
            span = int(W / 2 / ppd) + 2
            for o in range(int(cur) - span, int(cur) + span + 1):
                x = cx + (o - cur) * ppd
                dd = dt.date.fromordinal(max(1, o))
                fade = max(0.0, 1 - abs(x - cx) / (W / 2)) ** 0.6
                if dd.day == 1:
                    d.line([(x, base - 36), (x, base)], fill=INK + (int(255 * fade),), width=3)
                    d.text((x + 8, base - 38), MONTHS[dd.month - 1][:3].upper() +
                           (f" {dd.year}" if dd.month == 1 else ""), font=self.f_mon,
                           fill=INK + (int(235 * fade),))
                elif ppd >= 9 or dd.weekday() == 0:
                    h = 22 if dd.weekday() == 0 else 12
                    d.line([(x, base - h), (x, base)], fill=INK + (int(170 * fade),), width=2)
        else:
            # long jumps: a ruler of months and years instead of days
            y0 = dt.date.fromordinal(int(cur)).year
            span_y = int(W / 2 / (ppd * 365)) + 2
            for yy in range(y0 - span_y, y0 + span_y + 1):
                for mo in range(1, 13):
                    try:
                        o = dt.date(yy, mo, 1).toordinal()
                    except ValueError:
                        continue
                    x = cx + (o - cur) * ppd
                    if not (0 <= x <= W):
                        continue
                    fade = max(0.0, 1 - abs(x - cx) / (W / 2)) ** 0.6
                    if mo == 1:
                        d.line([(x, base - 36), (x, base)], fill=INK + (int(255 * fade),), width=3)
                        d.text((x + 8, base - 38), str(yy), font=self.f_mon,
                               fill=INK + (int(235 * fade),))
                    elif ppd * 30 >= 6:
                        d.line([(x, base - 10), (x, base)], fill=INK + (int(150 * fade),), width=2)
        d.polygon([(cx - 11, strip_top - 4), (cx + 11, strip_top - 4), (cx, strip_top + 12)],
                  fill=RED + (255,))
        d.line([(cx, strip_top + 8), (cx, BAND)], fill=RED + (255,), width=4)
        e = self.ev[idx] if idx >= 0 else self.ev[0]
        txt = e["label"] if arrived else self._rolling_label(cur, e)
        tw = d.textlength(txt, font=self.f_date)
        tm = e.get("time") if arrived else ""
        tmw = d.textlength(tm, font=self.f_time) + 26 if tm else 0
        pw = tw + tmw + 48
        py0, py1 = strip_top - 100, strip_top - 14
        d.rounded_rectangle([cx - pw / 2, py0, cx + pw / 2, py1], radius=6, fill=(8, 7, 7, 215))
        pulse = (_ease((t - e["at"]) / 0.15) * (1 - _ease((t - e["at"] - 0.25) / 0.6))
                 if arrived else 0)
        col = (tuple(int(INK[c] + (RED[c] - INK[c]) * pulse) for c in range(3))
               if arrived else DIM)
        x0 = cx - pw / 2 + 24
        d.text((x0, py0 + 8), txt, font=self.f_date, fill=col + (255,))
        if tm:
            ta = _ease((t - e["at"] - 0.15) / 0.3)
            d.text((x0 + tw + 26, py0 + 24), tm, font=self.f_time, fill=RED + (int(255 * ta),))
        if arrived:
            ua = _ease((t - e["at"]) / 0.35)
            d.line([(cx - pw / 2, py1), (cx - pw / 2 + pw * ua, py1)], fill=RED + (255,), width=4)
        out = Image.new("RGBA", (W, BAND), (0, 0, 0, 0))
        off = int((1 - up) * BAND)
        if off < BAND:
            out.alpha_composite(im.crop((0, 0, W, BAND - off)), (0, off))
        out.putalpha(out.getchannel("A").point(lambda v: int(v * min(1, up * 1.4))))
        return out

    def sound_cues(self):
        """("roll", start, span, days) under each scroll and ("datehit", t)
        on each arrival, for the sound-design bed."""
        cues = []
        for e in self.ev:
            days = int(abs(e["ord"] - e["prev"]))
            if days > 0:
                cues.append(("roll", e["at"] - SCROLL, SCROLL, min(days, 40)))
            cues.append(("datehit", e["at"]))
        return cues


def render_overlays(events, build_dir):
    """[(start_seconds, clip_path)] — one transparent clip per stretch the
    bar is up — plus the Ticker (for periods and sound cues)."""
    tk = Ticker(events)
    clips = []
    for n, (a, b) in enumerate(tk.periods()):
        work = tempfile.mkdtemp(prefix="ticker_", dir=build_dir)
        try:
            frames = int(math.ceil((b - a) * FPS)) + 1
            blank = None
            for fi in range(frames):
                img = tk.frame(a + fi / FPS)
                if img is None:
                    blank = blank or Image.new("RGBA", (W, BAND), (0, 0, 0, 0))
                    img = blank
                img.save(os.path.join(work, f"f{fi:04d}.png"))
            out = os.path.join(build_dir, f"lf_ticker_{n:02d}.mov")
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS),
                            "-i", os.path.join(work, "f%04d.png"), "-c:v", "qtrle",
                            "-pix_fmt", "argb", out], check=True)
            clips.append((a, out))
        finally:
            shutil.rmtree(work, ignore_errors=True)
    return clips, tk
