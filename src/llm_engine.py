import os
import json
import re
import time
from openai import OpenAI

# Free models rotate on OpenRouter. Primary + fallbacks so one deprecation
# doesn't kill the daily pipeline.
MODEL_CANDIDATES = [
    "meta-llama/llama-3.1-8b-instruct:free",
    "meta-llama/llama-3-8b-instruct:free",
    "mistralai/mistral-7b-instruct:free",
]

SYSTEM_PROMPT = """
You are an expert YouTube Shorts writer specializing in dark, cinematic history and bizarre strategy blunders.
Your goal is to write a high-tension, 50-second script.

You MUST output ONLY a valid JSON object with a single key "scenes" containing an array of objects.
Each object must have exactly two keys:
1. "narration": The dramatic text to be spoken (keep sentences punchy and suspenseful).
2. "image_prompt": A highly detailed prompt for an AI image generator. Always end the prompt with: "dark, cinematic, gritty oil painting style, masterpiece, 8k resolution".

Do NOT wrap the JSON in markdown code fences. Do NOT add any commentary before or after the JSON.

Example format:
{
  "scenes": [
    {
      "narration": "In 1219, a single arrogant mistake erased an empire of millions.",
      "image_prompt": "A massive medieval city burning at night, towering walls, shadowed figures, dark, cinematic, gritty oil painting style, masterpiece, 8k resolution"
    }
  ]
}
"""

USER_PROMPT = (
    "Write a 5-scene YouTube Short script about a bizarre historical paradox "
    "or a disastrous military strategy blunder. Make it mysterious and gripping."
)


def _extract_json(raw: str) -> dict | None:
    """
    Free models often ignore response_format and wrap output in ```json fences
    or add chatter. Strip fences, then fall back to grabbing the outermost
    {...} block before parsing.
    """
    if not raw:
        return None

    # 1. Strip markdown fences if present
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.MULTILINE).strip()

    # 2. Direct parse attempt
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # 3. Extract the outermost JSON object from surrounding chatter
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(cleaned[start:end + 1])
        except json.JSONDecodeError:
            pass

    return None


def _validate_script(data: dict) -> bool:
    """Guarantee downstream engines get exactly the structure they expect."""
    scenes = data.get("scenes") if isinstance(data, dict) else None
    if not isinstance(scenes, list) or not scenes:
        return False
    for scene in scenes:
        if not isinstance(scene, dict):
            return False
        if not scene.get("narration") or not scene.get("image_prompt"):
            return False
    return True


def generate_video_script(max_attempts_per_model: int = 2):
    """
    Calls OpenRouter to generate a YouTube Short script formatted as JSON.
    Tries multiple free models with retries and validates the output schema.
    """
    print("🧠 Initializing LLM Engine...")

    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        print("❌ OPENROUTER_API_KEY is not set.")
        return None

    client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key)

    for model in MODEL_CANDIDATES:
        for attempt in range(1, max_attempts_per_model + 1):
            try:
                print(f"🤖 Requesting script from {model} (attempt {attempt})...")
                response = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": USER_PROMPT},
                    ],
                    temperature=0.7,
                )

                raw = response.choices[0].message.content
                script_data = _extract_json(raw)

                if script_data and _validate_script(script_data):
                    print(f"✅ Script generated successfully ({len(script_data['scenes'])} scenes)!")
                    return script_data

                print("⚠️ Model returned malformed or incomplete JSON, retrying...")

            except Exception as e:
                print(f"⚠️ {model} attempt {attempt} failed: {e}")
                time.sleep(3)

    print("❌ All models exhausted. Script generation failed.")
    return None


if __name__ == "__main__":
    script = generate_video_script()
    if script:
        print(json.dumps(script, indent=2))
