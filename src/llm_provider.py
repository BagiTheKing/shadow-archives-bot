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

ONE PROVIDER IS ALSO A SINGLE POINT OF *BILLING* FAILURE. Chaining Gemini to
OpenRouter fixed outages but not allowances: both are free tiers, both run dry,
and they run dry on the same busy afternoon. So the chain now spans five free
providers and one paid one, and every tier is optional — configure none of the
new ones and the pipeline behaves exactly as before.

A run on 2026-09-17 lost every free layer AT ONCE: Gemini overloaded, Groq
listing models this key can't call, Cerebras out of credit, and — the one
that actually mattered, because it is supposed to be the deep catch-all —
every OpenRouter free model came back with empty content on the same
afternoon. OpenRouter is itself a single piece of infrastructure sitting in
front of dozens of models; when OpenRouter's OWN layer has a bad day, every
model behind it goes down together, no matter how many of them there are.
Zhipu is not behind OpenRouter — a separate company, a separate API, a
separate outage domain — which is the actual property this pipeline needed
that day, not just "one more model."

    gemini      free, no card. The best writer of the free tiers.
    groq        free, no card. Very fast, generous daily allowance.
    cerebras    free, no card. Another independent allowance.
    zhipu       free, no card, email sign-up only — see the base URL warning
                below. A Chinese provider on its own infrastructure,
                independent of every other layer here, OpenRouter included.
    openrouter  free, ~50 requests a day across whatever is free that morning.
    anthropic   PAID, and last on purpose — it is the only tier that can cost
                money, so it is only ever reached when every free tier has
                already refused. Unset by default.

A note on subscriptions, because it is the most common misunderstanding here: a
Claude Pro or a Gemini Advanced subscription is a chat plan for the web app. It
does not issue an API key and cannot be used by this pipeline. Anthropic API
billing is a separate, pay-as-you-go account; Google's AI Studio key is the free
tier this file has always preferred.

Get keys — all free, none need a card:
    Gemini    https://aistudio.google.com/apikey
    Groq      https://console.groq.com/keys
    Cerebras  https://cloud.cerebras.ai
    Zhipu     https://z.ai/model-api — sign up here, NOT
              open.bigmodel.cn. Zhipu runs two separate portals: the
              mainland-China one (open.bigmodel.cn) demands a verified
              Chinese phone number and real-name ID tied to a Chinese
              resident ID card; the international one (z.ai) takes just an
              email. Confirmed by cross-checking several independent
              sources in September 2026, not just one page — GLM-4.5-Flash
              and GLM-4.7-Flash are priced at $0 (input, output, and cached
              input) on z.ai specifically, genuinely free rather than a
              trial credit. Pricing on sites like this changes without
              notice, so if this tier ever stops working, that is the
              first thing worth re-checking at the live z.ai pricing page.

              Two things on the z.ai site look like they contradict this
              and don't. The model showcase (Language/Vision/Voice Models
              tabs) and the "Usage Bundles" prepaid packs both advertise
              the current flagship generation only — GLM-5.x, all paid, no
              free bundle offered. That page is selling the flagship, not
              listing everything the API serves. The free GLM-4.x-Flash
              models are the PREVIOUS generation, priced separately on the
              per-model API pricing table, and no bundle purchase is
              needed to call them — $0 means $0, nothing to prepay. Real
              risk, not a hypothetical one: GLM-4.7-Flash was dropped by at
              least one third-party host (DeepInfra) in September 2026
              citing low usage, which is exactly the shape of thing that
              could happen on z.ai's own API next with no announcement.
              That is what ZHIPU_MODELS and this tier's position behind
              four other free layers are both for — a 404 here is treated
              as a permanent failure per-model (see PERMANENT_STATUSES) and
              the chain moves on, it does not end the run.
"""

import os
import re

from openai import OpenAI

# Gemini's OpenAI-compatible surface. Same request and response shapes, so the
# calling code needs no provider-specific branches.
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
# Both speak the OpenAI protocol, including GET /models, so neither needs a
# hand-written model list — see the "model names are not stable" lesson above.
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
CEREBRAS_BASE_URL = "https://api.cerebras.ai/v1"
# The INTERNATIONAL portal. api.z.ai, not open.bigmodel.cn — that second one
# is Zhipu's mainland-China endpoint and requires a verified Chinese phone
# number plus real-name ID tied to a Chinese resident ID card. z.ai takes
# just an email. Getting this domain wrong doesn't fail loudly: it is a
# real, differently-authenticated service, not a typo that 404s.
ZHIPU_BASE_URL = "https://api.z.ai/api/paas/v4"

# Only used if live discovery fails. Deliberately generic aliases, which Google
# keeps pointed at a current model, rather than pinned versions that retire.
GEMINI_FALLBACK_MODELS = ["gemini-flash-latest", "gemini-2.0-flash"]

# Model families that cannot write a documentary script.
_GEMINI_SKIP = ("embedding", "aqa", "imagen", "image", "vision", "tts",
                "audio", "live", "veo", "learnlm", "gemma")

GEMINI_MAX_MODELS = 4


# Days (UTC) on which this run leaves Gemini alone. Gemini's free tier is 20
# requests a day per model, about 80 across the chain, and a story-mode
# long-form video needs about 62 of them on its own (trending lane run #48,
# 2026-09-28). The daily Shorts workflow sets this to the long-form lanes'
# days, so on those days the Short is written by Groq, Cerebras and the rest
# of the chain, and the whole Gemini allowance is the long-form video's. The
# long-form workflows never set it. Empty: Gemini is used every day.
GEMINI_RESERVED_DAYS = [d.strip().lower()[:3] for d in
                        os.getenv("GEMINI_RESERVED_DAYS", "").split(",")
                        if d.strip()]
_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_reserved_notice_shown = False


def gemini_reserved() -> bool:
    """Whether today (UTC) is one of GEMINI_RESERVED_DAYS."""
    import datetime
    today = _WEEKDAYS[datetime.datetime.now(datetime.timezone.utc).weekday()]
    return today in GEMINI_RESERVED_DAYS


# A second Gemini key (owner, 2026-09-30): its models join the chain right
# after the first key's, so a run that spends one key's daily quota carries on
# with the other. Its model ids carry the suffix below inside the pool (two
# keys serve the same model names); ModelPool.api_name strips it for the API.
GEMINI_KEY_ENVS = ("GEMINI_API_KEY", "GEMINI_API_KEY_2")
SECOND_KEY_SUFFIX = "@2"


def gemini_available() -> bool:
    global _reserved_notice_shown
    if not any(os.getenv(k) for k in GEMINI_KEY_ENVS):
        return False
    if gemini_reserved():
        if not _reserved_notice_shown:
            print("🔒 Gemini is reserved for today's long-form video "
                  "(GEMINI_RESERVED_DAYS) — this run writes with the rest "
                  "of the chain.")
            _reserved_notice_shown = True
        return False
    return True


def openrouter_available() -> bool:
    return bool(os.getenv("OPENROUTER_API_KEY"))


def groq_available() -> bool:
    return bool(os.getenv("GROQ_API_KEY"))


def cerebras_available() -> bool:
    return bool(os.getenv("CEREBRAS_API_KEY"))


def zhipu_available() -> bool:
    return bool(os.getenv("ZHIPU_API_KEY"))


def anthropic_available() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY"))


# Model families that cannot write a documentary script, on any provider that
# lists its catalogue. Guard/moderation models and speech models both appear in
# Groq's listing and both fail in ways that look like a bad script rather than a
# wrong model.
_CHAT_SKIP = ("whisper", "tts", "guard", "embed", "moderation", "vision",
              "audio", "distil", "prompt-guard", "allam")


def _discover_openai_models(client, provider: str, limit: int = 3) -> list:
    """Chat models this key can use today, from the provider's own catalogue.

    Same reasoning as discover_gemini_models: a hand-written list of model IDs
    goes stale between writing it and running it, and a run that dies on
    "model not found" has written nothing.
    """
    override = os.getenv(f"{provider.upper()}_MODELS")
    if override:
        models = [m.strip() for m in override.split(",") if m.strip()]
        print(f"   🔧 {provider}: {provider.upper()}_MODELS override in effect "
              f"-> {models or '(empty after stripping — check that secret!)'}")
        return models
    try:
        # A live run had cerebras_available() report the key present, then
        # contribute silently zero models to the chain — no exception, no
        # "no usable models" line, nothing to tell us why. Both prints below
        # existed before that run; whichever one should have fired plainly
        # didn't, or fired against zero raw models the API itself returned.
        # Materializing the raw list (instead of iterating client.models.list()
        # directly) costs nothing on catalogues this small and means the two
        # counts below can never both go unprinted again.
        raw = list(client.models.list())
        ids = []
        for model in raw:
            # The whole ID, owner prefix included: Groq lists and serves
            # "openai/gpt-oss-20b", and the bare "gpt-oss-20b" this used to
            # keep (a split meant for Gemini's "models/..." names) answers
            # 404 "does not exist", which made every Groq model look like a
            # bad key.
            mid = getattr(model, "id", "") or ""
            name = mid.split("/")[-1].lower()
            if not mid or any(s in name for s in _CHAT_SKIP):
                continue
            ids.append(mid)
        if ids:
            # Longest name last: providers tend to suffix the specialised
            # variants, and the plain name is the general-purpose one.
            ids.sort(key=lambda m: (len(m.split("/")[-1]), m))
            chosen = ids[:limit]
            print(f"   🔍 {provider}: {len(raw)} model(s) in the catalogue, "
                  f"{len(ids)} usable, using {chosen}")
            return chosen
        print(f"⚠️ {provider} listed no usable chat models "
              f"({len(raw)} raw model(s) returned, {len(ids)} survived the "
              f"chat-model filter).")
    except Exception as e:
        print(f"⚠️ Could not list {provider} models "
              f"({type(e).__name__}: {e}) — skipping this provider.")
    return []


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


def _drop_alias_duplicates(ids: list) -> list:
    """Removes '-latest' aliases when the model they point at is also listed.

    Google's catalogue returns both 'gemini-flash-latest' and the pinned
    version it currently resolves to, and the pool treats them as two separate
    models. They are one model sharing one quota bucket, so the chain spent
    four calls a minute on it against a five-per-minute free-tier limit and
    locked itself out of its own best writer — the 429 gives the game away by
    naming 'gemini-3.7-flash' in the reply to a 'gemini-flash-latest' request.

    Only dropped when something pinned survives, so a catalogue of nothing but
    aliases still yields a usable list.
    """
    pinned = [i for i in ids if not i.endswith("-latest")]
    if not pinned:
        return ids
    dropped = [i for i in ids if i.endswith("-latest")]
    if dropped:
        print(f"↪️  Skipping alias model(s) {', '.join(dropped)}: same quota "
              f"bucket as a pinned model already in the chain.")
    return pinned


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
            ids = _drop_alias_duplicates(ids)
            ids.sort(key=_gemini_rank)
            return ids[:GEMINI_MAX_MODELS]
        print("⚠️ Gemini listed no usable text models — using fallback names.")
    except Exception as e:
        print(f"⚠️ Could not list Gemini models ({type(e).__name__}: {e}) — "
              f"using fallback names.")
    return list(GEMINI_FALLBACK_MODELS)


# ---------------------------------------------------------------------------
# The paid tier
#
# Anthropic does not speak the OpenAI protocol, and this pipeline has exactly
# one call shape: client.chat.completions.create(...) -> .choices[0].message.
# Rather than teach every call site a second shape, the official SDK is wrapped
# in an adapter that presents the shape the rest of the code already uses.
#
# This is the only tier that can spend money, which is why it sits last in the
# chain and why nothing reaches it until every free provider has refused. A run
# that never exhausts its free allowances never sends Anthropic a request.
# ---------------------------------------------------------------------------

# Opus is the strongest writer available to this pipeline by a wide margin, and
# a whole video is roughly a dozen requests of a few thousand tokens. Override
# with ANTHROPIC_MODEL if that trade looks different to you.
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-5")

# Low effort, deliberately: writing a 280-word chapter as JSON is not a
# reasoning problem, and thinking tokens come out of the same budget as the
# reply — the exact failure that truncated every Gemini chapter of a live run.
ANTHROPIC_EFFORT = os.getenv("ANTHROPIC_EFFORT", "low")


class _AnthropicMessage:
    def __init__(self, content):
        self.content = content
        self.reasoning = None
        self.reasoning_content = None


class _AnthropicChoice:
    def __init__(self, content, finish_reason):
        self.message = _AnthropicMessage(content)
        self.finish_reason = finish_reason


class _AnthropicResponse:
    def __init__(self, content, finish_reason):
        self.choices = [_AnthropicChoice(content, finish_reason)]


class AnthropicClient:
    """The official Anthropic SDK, wearing the OpenAI client's interface.

    Only the surface this pipeline actually calls is implemented: a single
    non-streaming completion with a system prompt and one user message.
    """

    def __init__(self, api_key: str):
        import anthropic                       # imported here so the package
        self._sdk = anthropic.Anthropic(api_key=api_key)   # stays optional
        self.chat = self                       # client.chat.completions...
        self.completions = self

    def create(self, model=None, messages=(), temperature=None,
               max_tokens=1024, **_ignored):
        system = " ".join(m["content"] for m in messages
                          if m.get("role") == "system")
        turns = [{"role": m["role"], "content": m["content"]}
                 for m in messages if m.get("role") != "system"]

        kwargs = {"model": model or ANTHROPIC_MODEL,
                  "max_tokens": max_tokens,
                  "messages": turns}
        if system:
            kwargs["system"] = system
        if ANTHROPIC_EFFORT:
            kwargs["output_config"] = {"effort": ANTHROPIC_EFFORT}

        try:
            reply = self._sdk.messages.create(**kwargs)
        except TypeError:
            # An older SDK that does not know output_config must not cost the
            # run a chapter — same guard the OpenAI path uses for
            # reasoning_effort.
            kwargs.pop("output_config", None)
            reply = self._sdk.messages.create(**kwargs)

        # Text blocks only: a thinking block is not part of the answer, and
        # concatenating it into the JSON is what makes the parse fail.
        text = "".join(b.text for b in reply.content
                       if getattr(b, "type", "") == "text")
        # The pipeline reads "length" to mean the reply was cut off.
        finish = "length" if reply.stop_reason == "max_tokens" else "stop"
        return _AnthropicResponse(text, finish)


def anthropic_client():
    """The paid client, or None when the SDK is not installed."""
    try:
        return AnthropicClient(os.getenv("ANTHROPIC_API_KEY"))
    except ImportError:
        print("⚠️ ANTHROPIC_API_KEY is set but the 'anthropic' package is "
              "not installed — skipping the paid tier.")
        return None
    except Exception as e:
        print(f"⚠️ Could not start the Anthropic client "
              f"({type(e).__name__}: {e}) — skipping the paid tier.")
        return None


# Statuses that will answer exactly the same way a second later, so a retry buys
# nothing but a wasted call and a sleep. A 429 is deliberately NOT here: that one
# means come back later, and coming back later is what the chain is for.
#
# 404 is the common case and the surprising one. Model names are discovered from
# each provider's own GET /models, so a 404 is the provider listing a model in
# its catalogue and then refusing to serve it to this key — 'compound',
# 'gpt-oss-20b' and 'qwen3.6-27b' all did exactly that in one live run, and each
# was asked twice.
PERMANENT_STATUSES = {
    401: "key rejected",
    402: "provider out of credit",
    403: "key not entitled to this model",
    404: "listed in the catalogue but not callable with this key",
}


def permanent_failure(exc) -> str:
    """Why this model cannot work today, or "" when a retry is worth it."""
    status = getattr(exc, "status_code", None)
    if status is None:
        match = re.search(r"Error code:\s*(\d{3})", str(exc))
        status = int(match.group(1)) if match else None
    return PERMANENT_STATUSES.get(status, "")


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

    @staticmethod
    def api_name(model):
        """The id to send to the API: the pool's id without the second-key
        suffix ("gemini-3.8-flash@2" -> "gemini-3.8-flash")."""
        return model.split(SECOND_KEY_SUFFIX)[0]

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
    """Every model this run may use, in preference order.

    Free tiers first, cheapest failure mode first within them, and the paid
    tier last. Every provider is optional: a key that is not set contributes
    nothing and costs nothing.
    """
    entries = []

    if gemini_available():
        for n, env in enumerate(GEMINI_KEY_ENVS):
            if not os.getenv(env):
                continue
            client = OpenAI(base_url=GEMINI_BASE_URL, api_key=os.getenv(env))
            suffix = SECOND_KEY_SUFFIX if n else ""
            try:
                models = discover_gemini_models(client)
            except Exception as e:
                print(f"   ⚠️ {env}: model list unavailable ({type(e).__name__}).")
                models = []
            for model in models:
                entries.append((model + suffix, client, "gemini"))

    if groq_available():
        client = OpenAI(base_url=GROQ_BASE_URL,
                        api_key=os.getenv("GROQ_API_KEY"))
        for model in _discover_openai_models(client, "groq"):
            entries.append((model, client, "groq"))

    if cerebras_available():
        client = OpenAI(base_url=CEREBRAS_BASE_URL,
                        api_key=os.getenv("CEREBRAS_API_KEY"))
        for model in _discover_openai_models(client, "cerebras"):
            entries.append((model, client, "cerebras"))

    if zhipu_available():
        # Not discovered like the other tiers on purpose. Every model a Groq
        # or Cerebras key can list is free for that key — discovery is safe
        # there because the worst case is a stale name. Zhipu hosts free and
        # PAID models under the same key and the same catalogue; a discovery
        # pass here could just as easily hand back a paid model as the free
        # one, silently spending money the whole reason for this tier is to
        # avoid. GLM-4.7-Flash and GLM-4.5-Flash are the two confirmed
        # genuinely-free (not trial-credit) models on the z.ai international
        # portal as of September 2026 — cross-checked across several
        # independent sources, not taken from one page — so both are pinned,
        # newest first. ZHIPU_MODELS overrides this the same way *_MODELS
        # does elsewhere, for whoever wants a different (and still
        # confirmed-free) model, or when Zhipu's own free lineup moves on
        # again.
        override = os.getenv("ZHIPU_MODELS")
        models = ([m.strip() for m in override.split(",") if m.strip()]
                  if override else ["glm-4.7-flash", "glm-4.5-flash"])
        client = OpenAI(base_url=ZHIPU_BASE_URL,
                        api_key=os.getenv("ZHIPU_API_KEY"))
        for model in models:
            entries.append((model, client, "zhipu"))

    if openrouter_available():
        from llm_engine import get_free_models
        client = OpenAI(base_url=OPENROUTER_BASE_URL,
                        api_key=os.getenv("OPENROUTER_API_KEY"))
        for model in get_free_models():
            entries.append((model, client, "openrouter"))

    # Last, and only if a key is set: this is the one tier that bills.
    if anthropic_available():
        client = anthropic_client()
        if client is not None:
            entries.append((ANTHROPIC_MODEL, client, "anthropic"))

    return ModelPool(entries)


# In preference order, matching get_pool.
_PROVIDERS = (
    ("gemini", gemini_available, "free"),
    ("groq", groq_available, "free"),
    ("cerebras", cerebras_available, "free"),
    ("zhipu", zhipu_available, "free"),
    ("openrouter", openrouter_available, "free"),
    ("anthropic", anthropic_available, "paid"),
)


def configured_providers() -> list:
    """The providers this run actually has keys for, best first."""
    return [name for name, available, _ in _PROVIDERS if available()]


def describe_provider() -> str:
    names = configured_providers()
    if not names:
        return "none configured"
    if len(names) == 1:
        return names[0]
    return f"{names[0]}, falling back to {', '.join(names[1:])}"
