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
                messages=[{"role": "user", "content": "Reply with just: OK"}],
                max_tokens=8,
            )
            text = reply.choices[0].message.content
            print(f"✅ {provider:12s} ({model}) — replied: {text!r}")
            results.append((provider, True))
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
