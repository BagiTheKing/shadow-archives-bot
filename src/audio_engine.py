import os
import subprocess
import json
from gtts import gTTS

def synthesize_text(text: str, output_path: str):
    """
    Uses Google Translate's free TTS API. 
    We use tld='co.uk' to give it a British accent, fitting the historical documentary vibe.
    """
    # gTTS is highly resilient to datacenter IP bans
    tts = gTTS(text=text, lang='en', tld='co.uk', slow=False)
    tts.save(output_path)

def get_audio_duration(file_path: str) -> float:
    """
    Uses system ffprobe to extract the exact duration of the audio file in seconds.
    """
    cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", file_path
    ]
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=True)
        return float(result.stdout.strip())
    except Exception as e:
        print(f"❌ Failed to read audio duration for {file_path}: {e}")
        return 0.0

def generate_audio_pipeline(script_data: dict, output_dir: str = "build") -> dict:
    """
    Processes the script JSON, generates MP3 files for each scene,
    and updates the JSON object with the exact duration of each audio file.
    """
    print("🎙️ Initializing Audio Engine...")
    os.makedirs(output_dir, exist_ok=True)
    
    scenes = script_data.get("scenes", [])
    
    for idx, scene in enumerate(scenes):
        audio_filename = f"scene_{idx}.mp3"
        audio_path = os.path.join(output_dir, audio_filename)
        text_to_speak = scene.get("narration", "")
        
        print(f"🗣️ Synthesizing Scene {idx}: '{text_to_speak[:30]}...'")
        
        # Execute the synchronous gTTS generation
        synthesize_text(text_to_speak, audio_path)
        
        # Calculate precise timing for the video engine
        duration = get_audio_duration(audio_path)
        
        # Inject structural data back into the scene block
        scene["audio_path"] = audio_path
        scene["duration"] = duration
        print(f"⏱️ Scene {idx} Audio Generated: {duration} seconds")
        
    print("✅ Audio pipeline execution complete.")
    return script_data

if __name__ == "__main__":
    # Quick test stub
    test_json = {
      "scenes": [
        {
          "narration": "In the winter of 1914, an entire army vanished without firing a single shot.",
        }
      ]
    }
    updated_data = generate_audio_pipeline(test_json)
    print(json.dumps(updated_data, indent=2))
