"""Standalone connectivity check for every writer provider — no video render.

Run via the "Test Writer Providers" workflow (workflow_dispatch) whenever a
key is added or rotated. Each configured provider gets one minimal real
request; a provider with no key is skipped, never counted as a failure.
"""

import sys

sys.path.insert(0, "src")

from llm_provider import get_pool  # noqa: E402


def main():
    pool = get_pool()
    if not pool:
        print("❌ No provider configured at all — every *_API_KEY is unset.")
        sys.exit(1)

    seen_providers = []
    results = []
    for model in pool.models:
        provider = pool.provider_for(model)
        if provider in seen_providers:
            continue  # one probe per provider is enough
        seen_providers.append(provider)
        client = pool.client_for(model)
        try:
            reply = client.chat.completions.create(
                model=model,
                messages=[{"role": "user",
                           "content": "Reply with just the word: OK"}],
                max_tokens=300,
            )
            message = reply.choices[0].message
            content = message.content
            # llm_engine.py (the Shorts writer) reads ONLY .content — it has
            # no fallback to a reasoning field. So that is the bar for "does
            # this provider actually work", not just "did the call succeed
            # without raising".
            if content and content.strip():
                print(f"✅ {provider:12s} ({model}) — replied: {content!r}")
                results.append((provider, True))
            else:
                # Some reasoning models put the real answer in a sibling
                # field instead of .content when the token budget runs out
                # mid-thought — this is the same failure shape the Sept 2026
                # OpenRouter outage had. Surface it instead of hiding it.
                alt = {f: getattr(message, f, None)
                       for f in ("reasoning", "reasoning_content")
                       if getattr(message, f, None)}
                finish = getattr(reply.choices[0], "finish_reason", None)
                print(f"⚠️  {provider:12s} ({model}) — call succeeded but "
                      f".content was empty (finish_reason={finish!r}); "
                      f"llm_engine.py would treat this as a failed attempt. "
                      f"Other fields present: {alt or '(none)'}")
                results.append((provider, False))
        except Exception as e:
            print(f"❌ {provider:12s} ({model}) — "
                  f"{type(e).__name__}: {e}")
            results.append((provider, False))

    print()
    ok = sum(1 for _, passed in results if passed)
    print(f"{ok}/{len(results)} configured provider(s) working.")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
