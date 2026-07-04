import os
import json
from openai import OpenAI

def generate_video_script():
    """
    Calls OpenRouter to generate a YouTube Short script formatted as JSON.
    Iterates through a list of free models to ensure maximum uptime.
    """
    print("🧠 Initializing LLM Engine...")
    
    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=os.getenv("OPENROUTER_API_KEY"),
    )

    system_prompt = """
    You are an expert YouTube Shorts writer specializing in dark, cinematic history and bizarre strategy blunders. 
    Your goal is to write a high-tension, 50-second script.
    
    You MUST output ONLY a valid JSON object with a single key "scenes" containing an array of objects. 
    Each object must have exactly two keys:
    1. "narration": The dramatic text to be spoken (keep sentences punchy and suspenseful).
    2. "image_prompt": A highly detailed prompt for an AI image generator. Always end the prompt with: "dark, cinematic, gritty oil painting style, masterpiece, 8k resolution".
    """

    user_prompt = "Write a 5-scene YouTube Short script about a bizarre historical paradox or a disastrous military strategy blunder. Make it mysterious and gripping. Output ONLY valid JSON."

    # A resilient array of OpenRouter's most stable free endpoints
    free_models = [
        "google/gemini-1.5-flash:free",
        "qwen/qwen-2.5-72b-instruct:free",
        "google/gemma-2-9b-it:free",
        "huggingfaceh4/zephyr-7b-beta:free"
    ]

    for model in free_models:
        print(f"🤖 Requesting script from {model}...")
        try:
            response = client.chat.completions.create(
                model=model, 
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                response_format={"type": "json_object"},
                temperature=0.7,
            )

            raw_content = response.choices[0].message.content
            
            # Sometimes models wrap JSON in markdown blocks, let's strip them safely
            if raw_content.startswith("```json"):
                raw_content = raw_content.replace("```json\n", "").replace("\n```", "")
            elif raw_content.startswith("```"):
                raw_content = raw_content.replace("```\n", "").replace("\n```", "")

            script_data = json.loads(raw_content)
            print(f"✅ Script generated successfully using {model}!")
            return script_data

        except Exception as e:
            print(f"⚠️ {model} failed: {e}")
            continue # Try the next model in the list

    print("❌ All free models exhausted. Script generation failed.")
    return None

if __name__ == "__main__":
    script = generate_video_script()
    if script:
        print(json.dumps(script, indent=2))
