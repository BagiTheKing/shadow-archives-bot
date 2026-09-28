"""
Probe the writer providers: can each key list its models, and can it call
them? One tiny request per model (a few tokens), so a broken key shows up
here instead of halfway through a video. Gemini is left out by default so the
probe never spends its small daily allowance; set PROBE_GEMINI=1 to include it.

Prints only provider names, model IDs and error messages, never a key.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import llm_provider as lp  # noqa: E402
from openai import OpenAI  # noqa: E402

PROVIDERS = [
    ("groq", "GROQ_API_KEY", lp.GROQ_BASE_URL),
    ("cerebras", "CEREBRAS_API_KEY", lp.CEREBRAS_BASE_URL),
    ("zhipu", "ZHIPU_API_KEY", lp.ZHIPU_BASE_URL),
]
if os.getenv("PROBE_GEMINI") == "1":
    PROVIDERS.insert(0, ("gemini", "GEMINI_API_KEY", lp.GEMINI_BASE_URL))


def call(client, model):
    try:
        r = client.chat.completions.create(
            # Room to think: reasoning models (gpt-oss, GLM) spend a small
            # budget on reasoning and answer with empty text.
            model=model, max_tokens=300, temperature=0,
            messages=[{"role": "user", "content": "Reply with the word OK."}])
        text = (r.choices[0].message.content or "").strip()
        if not text:
            return "⚠️ reachable, but the answer was empty"
        return f"✅ answered {text[:30]!r}"
    except Exception as e:
        reason = lp.permanent_failure(e) or ""
        return f"❌ {type(e).__name__}: {str(e)[:160]}" + (
            f" [{reason}]" if reason else "")


def main():
    working = 0
    for name, env, base in PROVIDERS:
        key = os.getenv(env)
        print(f"\n=== {name} ===")
        if not key:
            print(f"   (no {env} secret set)")
            continue
        client = OpenAI(base_url=base, api_key=key)
        if name == "zhipu":
            models = ["glm-4.7-flash", "glm-4.5-flash"]
        elif name == "gemini":
            models = lp.discover_gemini_models(client)
        else:
            try:
                listed = [m.id for m in client.models.list()]
                print(f"   catalogue: {len(listed)} model(s): {', '.join(listed)}")
            except Exception as e:
                print(f"   ❌ could not list models: {type(e).__name__}: {str(e)[:160]}")
                continue
            models = lp._discover_openai_models(client, name)
        for m in models:
            result = call(client, m)
            working += result.startswith("✅")
            print(f"   {m}: {result}")
    print(f"\n{working} model(s) answered.")


if __name__ == "__main__":
    main()
