import os
import json
import time
from openai import OpenAI

def generate_video_script(max_retries=3):
    """
    Calls OpenRouter to generate a YouTube Short script formatted as JSON.
    Uses the 'openrouter/free' dynamic router to automatically find a working model.
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

    for attempt in range(1, max_retries + 1):
        print(f"🤖 Requesting script via OpenRouter dynamic free pool (Attempt {attempt}/{max_retries})...")
        try:
            response = client.chat.completions.create(
                # This automatically routes to any currently online free model
                model="openrouter/free", 
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                # Enforces that the randomly selected model must support JSON output
                response_format={"type": "json_object"},
                temperature=0.7,
            )

            raw_content = response.choices[0].message.content
            
            # Print which model the API actually ended up using
            used_model = response.model
            print(f"✨ OpenRouter successfully routed us to: {used_model}")
            
            # Safely strip markdown blocks if the model wraps the JSON
            if raw_content.startswith("```json"):
                raw_content = raw_content.replace("```json\n", "").replace("\n```", "")
            elif raw_content.startswith("```"):
                raw_content = raw_content.replace("```\n", "").replace("\n```", "")

            script_data = json.loads(raw_content)
            print("✅ Script generated successfully!")
            return script_data

        except Exception as e:
            print(f"⚠️ Attempt {attempt} failed: {e}")
            if attempt < max_retries:
                time.sleep(2)
            continue 

    print("❌ All script generation attempts failed.")
    return None

if __name__ == "__main__":
    script = generate_video_script()
    if script:
        print(json.dumps(script, indent=2))
