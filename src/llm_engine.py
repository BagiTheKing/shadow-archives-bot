import os
import json
from openai import OpenAI

def generate_video_script():
    """
    Calls OpenRouter to generate a YouTube Short script formatted as JSON.
    Each scene contains narration for the TTS and an image prompt for the visual engine.
    """
    print("🧠 Initializing LLM Engine...")
    
    # OpenRouter acts as a drop-in replacement for OpenAI's API
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

    user_prompt = "Write a 5-scene YouTube Short script about a bizarre historical paradox or a disastrous military strategy blunder. Make it mysterious and gripping."

    try:
        response = client.chat.completions.create(
            # Using Meta's Llama 3 8B - it is incredibly fast and completely free on OpenRouter
            model="meta-llama/llama-3-8b-instruct:free", 
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            response_format={"type": "json_object"},
            temperature=0.7,
        )

        raw_content = response.choices[0].message.content
        script_data = json.loads(raw_content)
        
        print("✅ Script generated successfully!")
        return script_data

    except Exception as e:
        print(f"❌ Error generating script: {e}")
        return None

# Quick test block to ensure it works when run directly
if __name__ == "__main__":
    # Note: You will need to export your OPENROUTER_API_KEY in your local terminal to test this
    script = generate_video_script()
    if script:
        print(json.dumps(script, indent=2))
