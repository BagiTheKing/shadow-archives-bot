"""
Which language model writes the scripts.

The channel started on OpenRouter's free tier because it needs no decisions:
one key, fifty requests a day, whatever models happen to be free that morning.
The scripts showed it. A live run produced a chapter that was four-fifths the
same two sentences repeated, invented an admiral, and quoted a casualty figure
copied out of the prompt's own example.

Google's Gemini free tier is a large step up in quality for the same price, and
it speaks the OpenAI protocol, so it drops in behind the same client the rest
of the pipeline already uses. It is preferred whenever GEMINI_API_KEY is set;
OpenRouter remains the fallback and the default, so a repository with no new
secrets behaves exactly as before.

Get a key at https://aistudio.google.com/apikey — free, no card.
"""

import os

from openai import OpenAI

# Gemini's OpenAI-compatible surface. Same request and response shapes, so
# _call() in longform_llm needs no provider-specific branches.
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"

# Best-first. Flash is the free tier's workhorse and is far better at holding a
# brief across a long generation than the free models on OpenRouter; lite is
# the fallback when the daily allowance on flash runs out.
GEMINI_MODELS = [
    m.strip() for m in os.getenv(
        "GEMINI_MODELS",
        "gemini-2.5-flash,gemini-2.0-flash,gemini-2.5-flash-lite"
    ).split(",") if m.strip()
]

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def gemini_available() -> bool:
    return bool(os.getenv("GEMINI_API_KEY"))


def get_provider():
    """Returns (client, models, provider_name) for this run.

    The whole script is written by one provider: mixing them mid-video is how
    a documentary ends up with two different voices in it.
    """
    if gemini_available():
        client = OpenAI(base_url=GEMINI_BASE_URL,
                        api_key=os.getenv("GEMINI_API_KEY"))
        return client, list(GEMINI_MODELS), "gemini"

    from llm_engine import get_free_models
    client = OpenAI(base_url=OPENROUTER_BASE_URL,
                    api_key=os.getenv("OPENROUTER_API_KEY"))
    return client, get_free_models(), "openrouter"


def describe_provider() -> str:
    return ("Gemini (free tier)" if gemini_available()
            else "OpenRouter free models")
