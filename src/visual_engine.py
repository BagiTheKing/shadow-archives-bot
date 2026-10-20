"""
Visual engine — Pollinations with the `flux` model (their best free model),
full 1080x1920 resolution, real image validation, and a designed fallback frame
so a failed download never kills the video or shows an error page as a "photo".
"""

import base64
import io
import math
import os
import random
import re
import time
import urllib.parse

import requests
from PIL import Image, ImageDraw, ImageFilter

WIDTH, HEIGHT = 1080, 1920

# The channel's look, and the single source of truth for it. The script engines
# ask their models to end every prompt with this, but a model that ignores the
# instruction used to decide the channel's art direction by itself: the prompt
# went to the generator with whatever style words the model preferred, and
# "photorealistic, 8k" beats a painterly suffix appended after it. Style is now
# imposed here, on the way out, so the model cannot vote on it.
CANONICAL_STYLE = ("dramatic chiaroscuro lighting, dark moody cinematic oil "
                   "painting, visible expressive brushwork, rich texture, "
                   "muted color palette with one accent color, highly "
                   "detailed, completely free of any text, letters, words, "
                   "writing, numbers, captions, signage or watermarks — any "
                   "page, book, document, sign or screen in frame is blank "
                   "or angled away so no text is visible, and no flags, star "
                   "emblems or national insignia on vehicles or uniforms")
# (The insignia clause: a Yemen 1994 tank came out with a US white star, 20
# Oct. A generator's guess at a country's markings is wrong more often than
# right, and a wrong one reads as a factual error.)

# Style directions that fight the painterly look. Any comma-clause containing
# one of these is dropped from the prompt before CANONICAL_STYLE is applied —
# photoreal tokens in particular drag a free model straight back into the
# uncanny valley this channel keeps landing in.
_CONFLICTING_STYLE = re.compile(
    r"\b("
    r"photo\s?-?\s?realistic|photorealism|hyper\s?-?\s?realistic|realistic photo|"
    r"photograph|photography|photo|35\s?mm|film still|dslr|bokeh|lens|"
    r"3d\s?render|octane|unreal engine|blender|cgi|"
    r"digital art|concept art|vector|isometric|anime|manga|cartoon|comic|"
    r"studio shot|stock photo|"
    r"4k|8k|uhd|hdr|ultra\s?-?\s?detailed photo"
    r")\b", re.IGNORECASE)


def era_from_text(text: str):
    """Earliest four-digit year mentioned, or None. Used to date a story from
    its topic line when no explicit year was passed down."""
    years = [int(y) for y in re.findall(r"\b(1[0-9]{3}|20[0-2][0-9])\b", text or "")]
    return min(years) if years else None


def _era_clause(era_year) -> str:
    """The period instruction attached to every generated frame.

    Telling the script model 'this story happens in 1928' is a request it can
    forget by the time it writes the ninth image prompt — and a forgotten era
    is how a fifteenth-century invasion ended up illustrated with modern
    footage. Stamping the year onto the prompt at generation time is not a
    request, so it cannot be forgotten.
    """
    if not era_year:
        return ""
    decade = (int(era_year) // 10) * 10
    return (f"set in the year {era_year}, historically accurate {decade}s "
            f"period detail, period-accurate clothing architecture vehicles "
            f"weapons and technology, nothing modern anywhere in frame")


def build_generation_prompt(prompt: str, era_year=None) -> str:
    """The subject the model described, dated to its period, in exactly one
    style — ours.

    Splits on commas, drops any clause carrying a competing style direction,
    removes a canonical suffix the model may already have appended, then
    re-applies the era and the style once each. Subject wording is never
    touched, so the shot type and the described action all survive.
    """
    body = (prompt or "").replace(CANONICAL_STYLE, " ")
    kept = []
    for clause in body.split(","):
        clause = clause.strip()
        if not clause or _CONFLICTING_STYLE.search(clause):
            continue
        # Drop an era clause we stamped on a previous pass, so repeated
        # normalisation cannot stack "set in the year 1928" three times.
        if clause.lower().startswith(("set in the year", "historically accurate",
                                      "period-accurate", "nothing modern")):
            continue
        kept.append(clause)
    subject = ", ".join(kept).strip(" .,") or "dark historical scene"

    era = _era_clause(era_year)
    return f"{subject}, {era}, {CANONICAL_STYLE}" if era \
        else f"{subject}, {CANONICAL_STYLE}"

# Flux is trained around one megapixel. Asking it for a 1920x1080 frame (2.07 MP)
# is asking it to compose at twice the resolution it learned on, and the
# classic symptom is duplicated features — the extra hands and fingers that
# made the long-form frames unusable. Generating at ~1 MP and upscaling with
# Lanczos gives a coherent image at the same delivery size; the pipeline pushes
# a 1.2x Ken Burns zoom at most, so the resample is invisible in motion.
GEN_TARGET_PIXELS = 1_050_000

# Pollinations' `enhance` runs the prompt through an LLM before generating.
# That helps a three-word prompt and hurts these: every prompt here already
# carries a shot type, an era and a deliberate style suffix, and the rewrite
# routinely drops the style. Off by default, still switchable.
ENHANCE = os.getenv("POLLINATIONS_ENHANCE", "0") == "1"

# Identify the client. Anonymous requests from CI ranges are the first thing a
# free service sheds when it is under load.
GEN_HEADERS = {
    "User-Agent": ("ArchiveParadoxBot/1.1 "
                   "(https://github.com/BagiTheKing/shadow-archives-bot)"),
}

# Raise this and every generated frame costs another five attempts. Five
# variants cycle across five attempts, so a real outage is detected but a
# transient 500 no longer produces a black screen.
GEN_MAX_RETRIES = 5


def _generation_size(width: int, height: int):
    """The nearest ~1 MP size with the requested aspect ratio, rounded to the
    multiples of 16 that diffusion models expect."""
    scale = (GEN_TARGET_PIXELS / (width * height)) ** 0.5
    if scale >= 1.0:
        return width, height          # already at or under native — leave it
    gen_w = max(512, int(round(width * scale / 16)) * 16)
    gen_h = max(512, int(round(height * scale / 16)) * 16)
    return gen_w, gen_h


# ---------------------------------------------------------------------------
# Cloudflare Workers AI — the preferred generator when it is configured.
#
# Pollinations is keyless, which is why the channel started there, but its
# free flux endpoint is the quality ceiling everything else keeps running into.
# Workers AI serves FLUX-1-schnell on a free daily allowance that comfortably
# covers a daily Short plus two weekly long-forms, and the same painterly
# prompt renders visibly cleaner through it.
#
# Entirely optional: with no credentials the pipeline behaves exactly as
# before. Set CF_ACCOUNT_ID and CF_API_TOKEN to switch it on.
# ---------------------------------------------------------------------------

CF_MODEL = os.getenv("CF_IMAGE_MODEL", "@cf/black-forest-labs/flux-1-schnell")

# schnell is distilled for very few steps; past ~8 it costs allowance without
# improving the picture, and Workers AI rejects more than 8.
CF_STEPS = int(os.getenv("CF_IMAGE_STEPS", "6"))

# Which Workers AI models accept width and height.
#
# flux-1-schnell does NOT. Sending them does not get them ignored, it fails
# the whole request:
#
#   AiError: Bad input: Error: Additional or unevaluated properties
#   '/width, /height' at '/' not allowed
#
# Which is what every single generation did for as long as this integration
# has existed — silently falling back to Pollinations on every frame, so the
# configured upgrade never rendered one pixel. flux returns a square; the
# caller crops it to frame.
CF_MODELS_ACCEPTING_DIMENSIONS = ("stable-diffusion", "dreamshaper",
                                  "lightning", "sdxl")


def _cf_accepts_dimensions(model: str) -> bool:
    low = model.lower()
    return any(family in low for family in CF_MODELS_ACCEPTING_DIMENSIONS)


def _cf_credentials():
    account = os.getenv("CF_ACCOUNT_ID") or os.getenv("CLOUDFLARE_ACCOUNT_ID")
    token = os.getenv("CF_API_TOKEN") or os.getenv("CLOUDFLARE_API_TOKEN")
    return (account, token) if account and token else (None, None)


def cloudflare_available() -> bool:
    return all(_cf_credentials())


def _decode_cf_response(response) -> bytes:
    """Workers AI returns either raw image bytes or JSON carrying base64,
    depending on the model and the day. Handle both rather than betting."""
    content_type = response.headers.get("content-type", "")
    if content_type.startswith("image/"):
        return response.content
    payload = response.json()
    if not payload.get("success", True):
        raise ValueError(str(payload.get("errors"))[:200])
    image = (payload.get("result") or {}).get("image")
    if not image:
        raise ValueError("no image in Workers AI response")
    return base64.b64decode(image)


def cloudflare_generate(prompt: str, width: int, height: int):
    """One image from Workers AI, or None so the caller falls back to
    Pollinations. Never raises: a generator outage must not end a run."""
    account, token = _cf_credentials()
    if not account:
        return None
    url = (f"https://api.cloudflare.com/client/v4/accounts/{account}"
           f"/ai/run/{CF_MODEL}")
    payload = {"prompt": prompt, "steps": CF_STEPS}
    if _cf_accepts_dimensions(CF_MODEL):
        payload["width"], payload["height"] = width, height
    try:
        response = requests.post(
            url,
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json"},
            json=payload,
            timeout=120)
        if response.status_code != 200:
            print(f"⚠️ Workers AI {response.status_code}: "
                  f"{response.text[:160]}")
            return None
        return _decode_cf_response(response)
    except Exception as e:
        print(f"⚠️ Workers AI failed ({type(e).__name__}: {e}) — "
              f"falling back to Pollinations.")
        return None


def _fit_cover(img: Image.Image, width: int, height: int) -> Image.Image:
    """Scale-and-crop to exactly width x height without distorting.

    Needed because flux-1-schnell may ignore the requested aspect ratio and
    hand back a square. Stretching a square to 16:9 makes every face wrong,
    so it is cropped instead.
    """
    if (img.width, img.height) == (width, height):
        return img
    src, dst = img.width / img.height, width / height
    if src > dst:
        new_w = int(img.height * dst)
        img = img.crop(((img.width - new_w) // 2, 0,
                        (img.width - new_w) // 2 + new_w, img.height))
    elif src < dst:
        new_h = int(img.width / dst)
        img = img.crop((0, (img.height - new_h) // 2,
                        img.width, (img.height - new_h) // 2 + new_h))
    return img.resize((width, height), Image.LANCZOS)


def _is_valid_image(content: bytes) -> bool:
    """Pollinations sometimes returns HTML error pages with status 200.
    Verify the bytes actually decode as a real image of a sane size."""
    try:
        img = Image.open(io.BytesIO(content))
        img.verify()
        img = Image.open(io.BytesIO(content))
        return img.width >= 512 and img.height >= 512
    except Exception:
        return False


def download_image(prompt: str, output_path: str, max_retries: int = GEN_MAX_RETRIES,
                   width: int = WIDTH, height: int = HEIGHT, era_year=None) -> bool:
    """Defaults to the 1080x1920 Shorts frame. The long-form pipeline passes
    16:9 dimensions instead — a vertical eight-minute documentary is a bad watch
    on desktop and TV, where search traffic actually lands.

    era_year dates the frame. Passing it here, rather than trusting the prompt
    to still carry the period, is what stops a 1928 story rendering in 2020s
    dress.

    Cloudflare Workers AI is tried first when configured, because its
    FLUX-1-schnell renders the same prompt visibly better than the keyless
    endpoint; Pollinations remains the fallback and the default."""
    final_prompt = build_generation_prompt(prompt, era_year)
    encoded = urllib.parse.quote(final_prompt)
    seed = random.randint(0, 999_999)  # new seed per scene -> no duplicate frames
    gen_w, gen_h = _generation_size(width, height)

    if cloudflare_available():
        for cf_attempt in range(2):
            blob = cloudflare_generate(final_prompt, gen_w, gen_h)
            if blob and _is_valid_image(blob):
                img = Image.open(io.BytesIO(blob)).convert("RGB")
                _fit_cover(img, width, height).save(output_path, "JPEG",
                                                    quality=93)
                return "workers-ai"
            if cf_attempt == 0:
                time.sleep(4)
        print("   ↩️ Workers AI unavailable — using Pollinations for this frame.")

    # A live run once returned HTTP 500 to all thirty generation requests,
    # because every attempt reused one parameter combination. Each attempt now
    # uses a different one, so a single unhappy option cannot cost the video
    # every generated frame.
    #
    # ORDER MATTERS FOR THE LOOK, not just for reliability. flux is the model
    # the channel's art direction is built around; turbo is a different model
    # with a different, cheaper look, so it sits last and only rescues a frame
    # that flux would otherwise not produce at all. Four flux shapes are tried
    # before the look is allowed to change.
    variants = [
        (f"?width={gen_w}&height={gen_h}&model=flux&nologo=true"
         f"&enhance={'true' if ENHANCE else 'false'}&seed={seed}", "flux"),
        (f"?width={gen_w}&height={gen_h}&model=flux&nologo=true&seed={seed}",
         "flux"),
        (f"?width=1024&height={int(1024 * height / width) // 16 * 16}"
         f"&model=flux&nologo=true&seed={seed}", "flux@1024"),
        (f"?width={gen_w}&height={gen_h}&nologo=true&seed={seed}", "default"),
        (f"?width={gen_w}&height={gen_h}&model=turbo&nologo=true&seed={seed}",
         "turbo"),
    ]

    for attempt in range(1, max_retries + 1):
        query, label = variants[(attempt - 1) % len(variants)]
        api_url = f"https://image.pollinations.ai/prompt/{encoded}{query}"
        try:
            response = requests.get(api_url, timeout=90, headers=GEN_HEADERS)
            if response.status_code == 200 and _is_valid_image(response.content):
                if label != "flux":
                    # Surfaced because it changes how the frame looks, and the
                    # run log is the only place that shows it.
                    print(f"   ℹ️ rendered by '{label}' (flux unavailable)")
                img = Image.open(io.BytesIO(response.content)).convert("RGB")
                if (img.width, img.height) != (width, height):
                    img = img.resize((width, height), Image.LANCZOS)
                img.save(output_path, "JPEG", quality=92)
                return f"pollinations/{label}"
            if response.status_code == 429:
                wait = 15 * attempt
                print(f"⚠️ Rate limited. Waiting {wait}s (attempt {attempt}/{max_retries})...")
                time.sleep(wait)
            else:
                print(f"⚠️ Bad response ({response.status_code} or invalid image). Retrying...")
                time.sleep(8)
        except Exception as e:
            print(f"⚠️ Network error: {e}. Retrying in 8s...")
            time.sleep(8)
    return ""


def make_fallback_image(output_path: str, seed: int = 0,
                        width: int = WIDTH, height: int = HEIGHT):
    """A moody dark-vignette gradient. Ugly-proof: if a download fails, the scene
    becomes an atmospheric 'black screen' moment instead of a broken frame.
    Dimensions are parameterised so the 16:9 long-form path is not handed a
    portrait frame to stretch."""
    rng = random.Random(seed)
    short_side = min(width, height)
    img = Image.new("RGB", (width, height), (8, 8, 12))
    draw = ImageDraw.Draw(img)
    # faint smoky blobs
    for _ in range(6):
        x, y = rng.randint(0, width), rng.randint(0, height)
        r = rng.randint(short_side // 5, short_side // 2)
        tone = rng.randint(18, 38)
        draw.ellipse([x - r, y - r, x + r, y + r], fill=(tone, tone, tone + 6))
    img = img.filter(ImageFilter.GaussianBlur(short_side // 9))
    # vignette
    mask = Image.new("L", (width, height), 0)
    pad = short_side // 4
    ImageDraw.Draw(mask).ellipse([-pad, -pad, width + pad, height + pad], fill=90)
    mask = mask.filter(ImageFilter.GaussianBlur(short_side // 5))
    black = Image.new("RGB", (width, height), (0, 0, 0))
    img = Image.composite(img, black, mask)
    img.save(output_path, "JPEG", quality=92)


# Shorts: a generated picture per this many seconds of narration, at most
# EXTRA_SHOTS_MAX more than the first. The real archive pictures
# (shorts_archive.py) add to these.
SHOT_PICTURE_SECONDS = float(os.getenv("SHORTS_PICTURE_SECONDS", "3.5"))
EXTRA_SHOTS_MAX = int(os.getenv("SHORTS_EXTRA_PICTURES", "3"))


def _vary_prompt(prompt: str, shot: int) -> str:
    """Another angle of the same moment, so the next shot is a new picture."""
    angles = ["", "extreme close-up detail of the scene, ",
              "wide establishing shot of the scene, ", "low-angle view, "]
    return angles[shot % len(angles)] + prompt


def generate_visual_pipeline(script_data: dict, output_dir: str = "build") -> dict:
    print("🎨 Initializing Visual Engine (flux @ 1080x1920)...")
    os.makedirs(output_dir, exist_ok=True)

    scenes = script_data.get("scenes", [])

    # Date the Short from its own topic line, so a story about 1928 is not
    # illustrated in modern dress. The long-form pipeline carries an explicit
    # era_year; here the topic string is the only thing that knows the period.
    era_year = era_from_text(f"{script_data.get('topic', '')} "
                             f"{script_data.get('title', '')}")
    if era_year:
        print(f"🕰️  Era anchor: ~{era_year}")

    for idx, scene in enumerate(scenes):
        image_path = os.path.join(output_dir, f"scene_{idx}.jpg")
        prompt = scene.get("image_prompt", "dark atmospheric historical scene, cinematic")

        # A scene may date itself. The outro card is the one that does, and it
        # has to: it advertises the NEXT episode, which is a different period
        # from the story just told. Episode 9 was the Iranian hostage crisis
        # and its card advertised the Enabling Act, so the card's frame was
        # generated under "set in the year 1979, 1970s period detail, nothing
        # modern anywhere in frame" — 1970s Iran drawn for a card about 1933
        # Germany, which reads as a bad render rather than a wrong one.
        scene_era = scene.get("era_year") or era_year
        if scene_era != era_year:
            print(f"   🕰️  scene {idx} dated separately: ~{scene_era}")

        print(f"🖼️ Generating image for scene {idx}...")
        source = download_image(prompt, image_path, era_year=scene_era)
        if source:
            print(f"📸 Saved {image_path} [{source}]")
        else:
            print(f"⚠️ Scene {idx}: all attempts failed — using designed fallback frame.")
            make_fallback_image(image_path, seed=idx)
            source = "designed-fallback"
        scene["image_path"] = image_path
        scene["image_paths"] = [image_path]
        # More pictures for a long scene: one per shot, each from a different
        # angle, so a 12-second scene is not one picture zoomed four times
        # (owner, 2026-10-08: "looks like lags").
        extra = 0 if scene.get("is_outro") else min(
            EXTRA_SHOTS_MAX, max(0, math.ceil((scene.get("duration") or 0) / SHOT_PICTURE_SECONDS) - 1))
        for k in range(1, extra + 1):
            more = os.path.join(output_dir, f"scene_{idx}_{k}.jpg")
            time.sleep(2)
            if download_image(_vary_prompt(prompt, k), more, era_year=scene_era):
                scene["image_paths"].append(more)
        if extra:
            print(f"   🖼️  scene {idx}: {len(scene['image_paths'])} picture(s) for "
                  f"{scene.get('duration', 0):.1f}s")
        # Which generator drew this frame. main.py gates the upload on it: the
        # look of the channel depends on WHICH model rendered the prompt, not
        # merely on whether a frame came back.
        scene["image_source"] = source

        if idx < len(scenes) - 1:
            time.sleep(6)  # stay under Pollinations' anonymous rate limit

    sources = [s.get("image_source", "") for s in scenes]
    preferred = sum(1 for s in sources if s == "workers-ai")
    if scenes and preferred < len(scenes):
        print(f"🎨 {preferred}/{len(scenes)} frames from Workers AI; the rest: "
              f"{', '.join(sorted(set(s for s in sources if s != 'workers-ai')))}")

    print("✅ Visual pipeline complete.")
    return script_data
