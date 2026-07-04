import os
import requests
import urllib.parse
import time

def download_image(prompt: str, output_path: str, width: int = 720, height: int = 1280, max_retries: int = 3):
    """
    Encodes the text prompt and fetches a freshly generated AI image.
    Includes robust error handling and retry logic for rate limits (429).
    """
    encoded_prompt = urllib.parse.quote(prompt)
    api_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width={width}&height={height}&nologo=true"
    
    for attempt in range(1, max_retries + 1):
        try:
            # Increased timeout to 45 seconds to give the free GPU time to render
            response = requests.get(api_url, timeout=45)
            
            if response.status_code == 200:
                with open(output_path, "wb") as f:
                    f.write(response.content)
                return True
            elif response.status_code == 429:
                print(f"⚠️ Rate limited (429). Retrying in 10 seconds... (Attempt {attempt}/{max_retries})")
                time.sleep(10)
            else:
                print(f"❌ API returned status code {response.status_code}. Retrying...")
                time.sleep(5)
                
        except Exception as e:
            print(f"❌ Network timeout or error: {e}. Retrying in 5 seconds...")
            time.sleep(5)
            
    return False

def generate_visual_pipeline(script_data: dict, output_dir: str = "build") -> dict:
    """
    Iterates through the script data, requests images with a respectful cooldown,
    and maps the assets to the JSON object.
    """
    print("🎨 Initializing Visual Engine...")
    os.makedirs(output_dir, exist_ok=True)
    
    scenes = script_data.get("scenes", [])
    
    for idx, scene in enumerate(scenes):
        image_filename = f"scene_{idx}.jpg"
        image_path = os.path.join(output_dir, image_filename)
        prompt = scene.get("image_prompt", "Dark atmospheric historical setting")
        
        print(f"🖼️ Fetching visual asset for Scene {idx}...")
        success = download_image(prompt, image_path)
        
        if success:
            scene["image_path"] = image_path
            print(f"📸 Saved image asset to {image_path}")
        else:
            print(f"⚠️ Using fallback mechanism for Scene {idx}")
            scene["image_path"] = None
            
        # Mandatory polite sleep between requests to avoid triggering the 429 rate limit again
        if idx < len(scenes) - 1:
            print("⏳ Sleeping for 5 seconds to respect API rate limits...")
            time.sleep(5)
            
    print("✅ Visual pipeline execution complete.")
    return script_data

if __name__ == "__main__":
    test_json = {
        "scenes": [
            {
                "image_prompt": "A shadowed emperor sitting on a crumbling throne, cinematic lighting, gritty oil painting style, masterpiece, 8k resolution"
            }
        ]
    }
    updated_data = generate_visual_pipeline(test_json)
    print(updated_data)
