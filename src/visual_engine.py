import os
import requests
import urllib.parse

def download_image(prompt: str, output_path: str, width: int = 720, height: int = 1280):
    """
    Encodes the text prompt and fetches a freshly generated AI image via 
    the Pollinations GET API endpoint, storing it locally.
    """
    # Clean and safely URL-encode the string prompt for query string integration
    encoded_prompt = urllib.parse.quote(prompt)
    
    # Using the flux model variation hosted by Pollinations for high fidelity asset generation
    api_url = f"https://image.pollinations.ai/p/{encoded_prompt}?width={width}&height={height}&seed=42&nologo=true"
    
    try:
        response = requests.get(api_url, timeout=30)
        if response.status_code == 200:
            with open(output_path, "wb") as f:
                f.write(response.content)
            return True
        else:
            print(f"❌ Pollinations API returned status code {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ Failed to fetch image asset: {e}")
        return False

def generate_visual_pipeline(script_data: dict, output_dir: str = "build") -> dict:
    """
    Iterates through the enriched script data, requests images for each prompt,
    and updates the pipeline directory mapping.
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
            
    print("✅ Visual pipeline execution complete.")
    return script_data

if __name__ == "__main__":
    # Simple verification test block
    test_json = {
        "scenes": [
            {
                "image_prompt": "A shadowed emperor sitting on a crumbling throne, cinematic lighting, gritty oil painting style, masterpiece, 8k resolution"
            }
        ]
    }
    updated_data = generate_visual_pipeline(test_json)
    print(updated_data)
