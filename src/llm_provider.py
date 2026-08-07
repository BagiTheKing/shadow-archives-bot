"""
Which language model writes the scripts.

The channel started on OpenRouter's free tier because it needs no decisions:
one key, fifty requests a day, whatever models happen to be free that morning.
The scripts showed it. A live run produced a chapter that was four-fifths the
same two sentences repeated, invented an admiral, and quoted a casualty figure
copied out of the prompt's own example.

Google's Gemini free tier is a large step up in quality for the same price, and
it speaks the OpenAI protocol, so it drops in behind the same client the rest of
the pipeline already uses.

Two hard lessons are baked in here.

MODEL NAMES ARE NOT STABLE. A hand-written list of Gemini model IDs went stale
between writing it and running it: two of the three returned "no longer
available to new users" and the third was out of quota, and the run died having
written nothing. The list is now discovered from the API at run time, and only
falls back to hard-coded names if discovery itself fails.

ONE PROVIDER IS A SINGLE POINT OF FAILURE. Preferring Gemini used to mean
*only* Gemini, so a bad afternoon at Google ended the run even with a working
OpenRouter key sitting right there. The pool now chains them: every Gemini
candidate first, then OpenRouter's free models, each with its own client. A
chapter is only lost when every model everywhere refuses it.

Get a Gemini key at https://aistudio.google.com/apikey — free, no card.
"""

import os

from openai import OpenAI

# Gemini's OpenAI-compatible surface. Same request and response shapes, so the
# calling code needs no provider-specific branches.
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Only used if live discovery fails. Deliberately generic aliases, which Google
# keeps pointed at a current model, rather than pinned versions that retire.
GEMINI_FALLBACK_MODELS = ["gemini-flash-latest", "gemini-2.0-flash"]

# Model families that cannot write a documentary script.
_GEMINI_SKIP = ("embedding", "aqa", "imagen", "image", "vision", "tts",
                "audio", "live", "veo", "learnlm", "gemma")

GEMINI_MAX_MODELS = 4


def gemini_available() -> bool:
    return bool(os.getenv("GEMINI_API_KEY"))


def openrouter_available() -> bool:
    return bool(os.getenv("OPENROUTER_API_KEY"))


def _gemini_rank(model_id: str):
    """Cheapest-and-fastest first, newest within that.

    Flash models carry the largest free-tier allowances, which is what this
    pipeline actually runs out of; pro is better prose but its free quota is
    spent within a chapter or two.
    """
    name = model_id.lower()
    tier = 0 if ("flash" in name and "lite" not in name) else \
           1 if "lite" in name else 2
    # Prefer explicitly-current aliases, then higher version numbers.
    alias = 0 if "latest" in name else 1
    version = 0.0
    for token in name.replace("-", " ").split():
        try:
            version = max(version, float(token))
        except ValueError:
            continue
    return (tier, alias, -version)


def discover_gemini_models(client) -> list:
    """Model IDs this key can actually use today, best first.

    Falls back to GEMINI_FALLBACK_MODELS when the listing call fails, so a
    transient error does not cost the run its preferred provider.
    """
    override = os.getenv("GEMINI_MODELS")
    if override:
        return [m.strip() for m in override.split(",") if m.strip()]

    try:
        listed = client.models.list()
        ids = []
        for model in listed:
            mid = getattr(model, "id", "") or ""
            mid = mid.split("/")[-1]          # 'models/gemini-x' -> 'gemini-x'
            low = mid.lower()
            if "gemini" not in low or any(s in low for s in _GEMINI_SKIP):
                continue
            ids.append(mid)
        if ids:
            ids.sort(key=_gemini_rank)
            return ids[:GEMINI_MAX_MODELS]
        print("⚠️ Gemini listed no usable text models — using fallback names.")
    except Exception as e:
        print(f"⚠️ Could not list Gemini models ({type(e).__name__}: {e}) — "
              f"using fallback names.")
    return list(GEMINI_FALLBACK_MODELS)


class ModelPool:
    """Ordered (client, model) candidates spanning every provider configured.

    The pipeline asks the pool for models and for the client that serves a
    given model, so provider chaining costs the call sites nothing.
    """

    def __init__(self, entries):
        self._entries = entries                      # [(model_id, client, provider)]
        self._by_model = {m: (c, p) for m, c, p in entries}

    def __bool__(self):
        return bool(self._entries)

    @property
    def models(self):
        return [m for m, _, _ in self._entries]

    def client_for(self, model):
        return self._by_model[model][0]

    def provider_for(self, model):
        return self._by_model[model][1]

    def describe(self):
        names, seen = [], set()
        for _, _, provider in self._entries:
            if provider not in seen:
                seen.add(provider)
                names.append(provider)
        return " -> ".join(names) if names else "none configured"


def get_pool() -> ModelPool:
    """Every model this run may use, in preference order."""
    entries = []

    if gemini_available():
        client = OpenAI(base_url=GEMINI_BASE_URL,
                        api_key=os.getenv("GEMINI_API_KEY"))
        for model in discover_gemini_models(client):
            entries.append((model, client, "gemini"))

    if openrouter_available():
        from llm_engine import get_free_models
        client = OpenAI(base_url=OPENROUTER_BASE_URL,
                        api_key=os.getenv("OPENROUTER_API_KEY"))
        for model in get_free_models():
            entries.append((model, client, "openrouter"))

    return ModelPool(entries)


def describe_provider() -> str:
    if gemini_available() and openrouter_available():
        return "Gemini free tier, falling back to OpenRouter"
    if gemini_available():
        return "Gemini (free tier)"
    return "OpenRouter free models"
