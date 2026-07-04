import os
import asyncio
import subprocess
import json
import edge_tts

VOICE_ACTOR = "en-US-ChristopherNeural"  # Deep, cinematic male voice perfect for history/tension
SPEECH_RATE = "+5%"                      # Slightly accelerated rate to optimize Shorts retention

async def synthesize_text(text: str, output_path: str):
    """
    Communicates with Microsoft's Edge TTS service to generate high-quality,
    natural-sounding speech without requiring an API key.
    """
    communicate = edge_tts.Communicate(text, VOICE_ACTOR, rate=SPEECH_RATE)
    await communicate.save(output_path)

def get_audio_duration(file_path: str) -> float:
    """
    Uses system ffprobe to extract the exact duration of the audio file in seconds.
    Ensures zero reliance on heavy external Python audio libraries.
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
        
        # Execute the asynchronous edge-tts generation inside the synchronous pipeline
        asyncio.run(synthesize_text(text_to_speak, audio_path))
        
        # Calculate precise timing
        duration = get_audio_duration(audio_path)
        
        # Inject structural data back into the scene block for the Video Engine
        scene["audio_path"] = audio_path
        scene["duration"] = duration
        print(f"⏱️ Scene {idx} Audio Generated: {duration} seconds")
        
    print("✅ Audio pipeline execution complete.")
    return script_data

if __name__ == "__main__":
    # Test stub representing output from the llm_engine
    test_json = {
      "scenes": [
        {
          "narration": "In the winter of 1914, an entire army vanished without firing a single shot.",
          "image_prompt": "Cinematic historical winter battle landscape..."
        },
        {
          "narration": "They didn't surrender. They didn't retreat. They simply marched into a trap of their own making.",
          "image_prompt": "Dark forest covered in deep snow..."
        }
      ]
    }
    updated_data = generate_audio_pipeline(test_json)
    print("\nModified Pipeline Metadata:")
    print(json.dumps(updated_data, indent=2))
