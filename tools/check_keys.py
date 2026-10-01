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
    # the last 4 characters only, to compare with AI Studio's key list
    print(f"{env}: key ends in ...{key[-4:]} ({len(key)} characters)")
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


def github_models():
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if not token:
        print("GitHub Models: no GITHUB_TOKEN")
        return
    import requests
    r = requests.get("https://models.github.ai/catalog/models", timeout=30,
                     headers={"Authorization": f"Bearer {token}",
                              "Accept": "application/vnd.github+json"})
    print(f"GitHub Models catalogue: HTTP {r.status_code}")
    if r.status_code != 200:
        print("   " + r.text[:200])
        return
    for m in r.json():
        if "chat-completion" not in str(m.get("supported_output_modalities", "")) and \
                "text" not in str(m.get("supported_output_modalities", "")):
            continue
        lim = m.get("limits") or {}
        print(f"   {m.get('id')}: tier {m.get('rate_limit_tier')}, "
              f"in {lim.get('max_input_tokens')}, out {lim.get('max_output_tokens')}")
    from openai import OpenAI
    client = OpenAI(base_url="https://models.github.ai/inference", api_key=token)
    for model in ("openai/gpt-4.1", "deepseek/DeepSeek-V3-0324", "openai/gpt-4.1-mini"):
        try:
            got = client.chat.completions.create(
                model=model, max_tokens=20,
                messages=[{"role": "user", "content": "Reply with the single word OK."}])
            print(f"   {model}: answered {got.choices[0].message.content.strip()[:20]!r}")
        except Exception as e:
            print(f"   {model}: FAILED — {str(e)[:200]}")


if __name__ == "__main__":
    which = sys.argv[1:] or ["gemini", "wikimedia", "github"]
    if "gemini" in which:
        gemini("GEMINI_API_KEY")
        gemini("GEMINI_API_KEY_2")
    if "wikimedia" in which:
        wikimedia()
    if "github" in which:
        github_models()
