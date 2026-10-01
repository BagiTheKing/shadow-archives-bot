"""
Progress saved between runs, so a run that dies (spent quota, a chapter no
model could write) is resumed by the next run of the same lane instead of
starting over and paying for the same research and chapters again.

What is kept, in RUN_CACHE_DIR/state.json (the workflow keeps that folder on
the `run-cache` branch, one folder per lane):
- the topic and its story (fact sheet, outline, claim sources),
- every chapter already written, keyed by its place in the outline,
- the finished script (packaging, quotes, maps: every LLM step done), and
- once rendered, the upload's metadata and the run whose artifact holds the
  render, so a failed upload is retried without writing or rendering again.

A finished upload clears it. Progress older than MAX_AGE_DAYS is ignored:
the story it belongs to may no longer be the right one to tell. Without
RUN_CACHE_DIR (local runs, previews) nothing is saved.
"""

import datetime as dt
import json
import os

MAX_AGE_DAYS = 4
MAX_RESUMES = 2


def _path():
    d = os.getenv("RUN_CACHE_DIR", "").strip()
    return os.path.join(d, "state.json") if d else None


def _now():
    return dt.datetime.now(dt.timezone.utc)


def load(fresh_check=True):
    """The saved progress, or None (nothing saved, too old, unreadable).
    fresh_check=False skips the resume limit: used while a resumed run reads
    and adds to the progress it is already carrying on."""
    p = _path()
    if not p or not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            state = json.load(f)
        age = _now() - dt.datetime.fromisoformat(state["saved"])
        if age.days >= MAX_AGE_DAYS or not state.get("topic") or not state.get("story"):
            return None
        if fresh_check and state.get("resumes", 0) >= MAX_RESUMES:
            # resumed twice and still not finished: something in this story
            # itself is the problem, so the lane starts on a new one
            print("   ↩️  Saved progress already resumed twice — starting fresh.")
            return None
        return state
    except Exception as e:
        print(f"   ↩️  Saved progress unreadable ({type(e).__name__}) — starting fresh.")
        return None


def _write(state):
    p = _path()
    if not p:
        return
    state["saved"] = _now().isoformat()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p + ".part", "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    os.replace(p + ".part", p)


def save_story(topic, story, enforce_history=False, era_year=None, from_short=False):
    """A new story starts a new save: any chapters of another topic go."""
    try:
        _write({"topic": topic, "story": story, "enforce_history": enforce_history,
                "era_year": era_year, "from_short": from_short, "chapters": {}})
    except Exception as e:
        print(f"   ↩️  Could not save progress ({type(e).__name__}: {e}).")


def save_chapter(topic, c_index, chapter):
    try:
        state = load(fresh_check=False)
        if not state or state["topic"] != topic:
            return
        state["chapters"][str(c_index)] = chapter
        _write(state)
    except Exception as e:
        print(f"   ↩️  Could not save chapter {c_index} ({type(e).__name__}: {e}).")


def chapter(topic, c_index, heading):
    """A saved chapter for this place in this story's outline, or None."""
    state = load(fresh_check=False)
    if not state or state["topic"] != topic:
        return None
    ch = state.get("chapters", {}).get(str(c_index))
    return ch if ch and ch.get("heading") == heading else None


def save_stage(topic, name, value):
    """A finished stage of this story ("script", "rendered")."""
    try:
        state = load(fresh_check=False)
        if not state or state["topic"] != topic:
            return
        state[name] = value
        _write(state)
        print(f"💾 Progress saved: {name}.")
    except Exception as e:
        print(f"   ↩️  Could not save {name} ({type(e).__name__}: {e}).")


def stage(topic, name):
    state = load(fresh_check=False)
    if not state or state["topic"] != topic:
        return None
    return state.get(name)


def mark_resumed():
    """Counts a resume, so a story that keeps failing is let go (MAX_RESUMES)."""
    try:
        state = load(fresh_check=False)
        if state:
            state["resumes"] = state.get("resumes", 0) + 1
            _write(state)
    except Exception:
        pass


def clear():
    p = _path()
    if p and os.path.exists(p):
        os.remove(p)
        print("🧹 Saved progress cleared (the video is up).")
