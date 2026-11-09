"""Appends today's result (posted / built / skipped) to library/log.json.

    python tools/library_log.py out
"""
import datetime as dt
import json
import os
import sys

LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "library", "log.json")


def entry(out):
    def rd(n):
        try:
            return json.load(open(os.path.join(out, n), encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return {}
    pick, skip = rd("pick.json"), rd("skip.json")
    url = ""
    try:
        url = open(os.path.join(out, "url.txt")).read().strip()
    except FileNotFoundError:
        pass
    r = {"date": dt.date.today().isoformat()}
    if url:
        r.update(status="posted", url=url)
    elif pick:
        r.update(status="not_posted", reason="built but not posted (check, render or upload failed, or post=false)")
    else:
        r.update(status="skipped", reason=skip.get("reason", "the run failed before choosing"))
    r.update({k: pick[k] for k in ("slug", "headline", "title") if k in pick})
    return r


if __name__ == "__main__":
    try:
        log = json.load(open(LOG, encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        log = []
    log.append(entry(sys.argv[1]))
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    json.dump(log[-200:], open(LOG, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(log[-1])
