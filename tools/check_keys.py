"""
Checks the keys the pipeline depends on, without printing any of them:
each Gemini key (can it list models and answer?) and the Wikimedia token
(does Wikipedia see us logged in?), then places a few map names the way a
run does.

    python tools/check_keys.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def gemini(env):
    key = os.getenv(env, "").strip()
    if not key:
        print(f"{env}: not set")
        return
    from openai import OpenAI
    from llm_provider import GEMINI_BASE_URL
    client = OpenAI(base_url=GEMINI_BASE_URL, api_key=key)
    try:
        models = [m.id for m in client.models.list()]
        flash = sorted(m.split("/")[-1] for m in models if "flash" in m and "image" not in m
                       and "tts" not in m and "live" not in m)
        print(f"{env}: {len(models)} models listed; flash: {', '.join(flash[-4:])}")
    except Exception as e:
        print(f"{env}: listing FAILED — {str(e)[:160]}")
        return
    for model in reversed(flash[-3:]):
        try:
            r = client.chat.completions.create(
                model=model, max_tokens=20,
                messages=[{"role": "user", "content": "Reply with the single word OK."}])
            print(f"   {model}: answered {r.choices[0].message.content.strip()[:20]!r}")
        except Exception as e:
            print(f"   {model}: FAILED — {str(e)[:160]}")


def wikimedia():
    from archive_engine import _polite_get
    r = _polite_get("https://en.wikipedia.org/w/api.php", params={
        "action": "query", "meta": "userinfo", "format": "json"}, timeout=30)
    info = (r.json().get("query") or {}).get("userinfo") or {}
    who = "ANONYMOUS (token not used or not accepted)" if "anon" in info \
        else f"logged in as {info.get('name')}"
    print(f"WIKIMEDIA_TOKEN: {'set' if os.getenv('WIKIMEDIA_TOKEN') else 'not set'}; "
          f"Wikipedia sees: {who} (HTTP {r.status_code})")
    import map_engine as m
    for name in ("Tobruk", "Herrlingen", "Badme", "Golan Heights", "Normandy",
                 "Devil's Island", "Pripyat", "North Africa"):
        print(f"   map place {name!r}: {m.geocode(name)}")


if __name__ == "__main__":
    gemini("GEMINI_API_KEY")
    gemini("GEMINI_API_KEY_2")
    wikimedia()
