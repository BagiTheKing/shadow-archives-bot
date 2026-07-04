import os
import subprocess

def format_ass_time(seconds: float) -> str:
    """Converts seconds into the specific timestamp format required by .ass files (H:MM:SS.cs)"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    centiseconds = int(round((seconds - int(seconds)) * 100))
    return f"{hours}:{minutes:02d}:{secs:02d}.{centiseconds:02d}"

def generate_subtitles(script_data: dict, template_path: str, output_ass: str):
    """Reads the audio durations and narration to build a perfectly timed subtitle file."""
    print("📝 Generating subtitle timeline...")
    
    with open(template_path, 'r', encoding='utf-8') as f:
        ass_content = f.read()
        
    current_time = 0.0
    events = []
    
    for scene in script_data.get("scenes", []):
        duration = scene.get("duration", 0)
        text = scene.get("narration", "")
        
        start_time_str = format_ass_time(current_time)
        end_time_str = format_ass_time(current_time + duration)
        
        events.append(f"Dialogue: 0,{start_time_str},{end_time_str},Default,,0,0,0,,{text}")
        current_time += duration
        
    with open(output_ass, 'w', encoding='utf-8') as f:
        f.write(ass_content + "\n" + "\n".join(events))
        
    print(f"✅ Subtitles saved to {output_ass}")

def create_scene_clip(image_path: str, audio_path: str, duration: float, output_path: str):
    """Uses FFmpeg to combine an image and audio file, applying a slow zoom effect."""
    zoom_filter = f"zoompan=z='min(zoom+0.0015,1.15)':d={int(duration*30)}:s=720x1280:fps=30"
    
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", image_path,
        "-i", audio_path,
        "-vf", zoom_filter,
        "-c:v", "libx264", "-tune", "stillimage", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        "-t", str(duration),
        "-shortest",
        output_path
    ]
    
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, check=True)

def generate_video_pipeline(script_data: dict, build_dir: str = "build") -> str:
    """Orchestrates the entire rendering process and returns the path to the final video."""
    print("🎬 Initializing Video Engine...")
    
    scenes = script_data.get("scenes", [])
    clip_paths = []
    
    for idx, scene in enumerate(scenes):
        image_path = scene.get("image_path")
        audio_path = scene.get("audio_path")
        duration = scene.get("duration")
        
        if not image_path or not audio_path:
            print(f"⚠️ Skipping Scene {idx}: Missing assets.")
            continue
            
        clip_path = os.path.join(build_dir, f"clip_{idx}.mp4")
        print(f"🎞️ Rendering clip for Scene {idx} ({duration}s)...")
        create_scene_clip(image_path, audio_path, duration, clip_path)
        clip_paths.append(clip_path)

    print("🔗 Concatenating scenes...")
    concat_list_path = os.path.join(build_dir, "concat_list.txt")
    with open(concat_list_path, "w") as f:
        for cp in clip_paths:
            # FIX: Convert to absolute path so FFmpeg doesn't get lost
            abs_path = os.path.abspath(cp).replace("\\", "/")
            f.write(f"file '{abs_path}'\n")

    raw_output = os.path.join(build_dir, "raw_output.mp4")
    concat_cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", concat_list_path,
        "-c", "copy", raw_output
    ]
    subprocess.run(concat_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, check=True)

    ass_path = os.path.join(build_dir, "subtitles.ass")
    template_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "template.ass")
    generate_subtitles(script_data, template_path, ass_path)

    print("🔥 Burning subtitles into final video...")
    final_output = os.path.join(build_dir, "final_short.mp4")
    
    # FIX: Simplify path escaping for the subtitle filter
    safe_ass_path = os.path.abspath(ass_path).replace("\\", "/").replace(":", "\\:")
    burn_cmd = [
        "ffmpeg", "-y", "-i", raw_output,
        "-vf", f"ass='{safe_ass_path}'",
        "-c:a", "copy",
        final_output
    ]
    subprocess.run(burn_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, check=True)

    print(f"✅ Video Pipeline Complete! Final file: {final_output}")
    return final_output
