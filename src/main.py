import os
import shutil
from llm_engine import generate_video_script
from audio_engine import generate_audio_pipeline
from visual_engine import generate_visual_pipeline
from video_engine import generate_video_pipeline
from youtube_publisher import upload_video_to_youtube

def cleanup_build_dir(build_dir="build"):
    """Wipes the build directory clean before starting to avoid lingering files."""
    if os.path.exists(build_dir):
        shutil.rmtree(build_dir)
    os.makedirs(build_dir)

def main():
    print("🚀 Initiating The Shadow Archives Automated Pipeline...")
    build_dir = "build"
    cleanup_build_dir(build_dir)

    # Phase 1: Script & Logic
    script_data = generate_video_script()
    if not script_data:
        print("❌ Pipeline halted: Script generation failed.")
        return

    # Phase 2: Asset Generation
    script_data = generate_audio_pipeline(script_data, build_dir)
    script_data = generate_visual_pipeline(script_data, build_dir)

    # Phase 3: Video Rendering
    final_video_path = generate_video_pipeline(script_data, build_dir)

    # Phase 4: Metadata & Upload
    first_scene_text = script_data["scenes"][0]["narration"]
    title = f"{first_scene_text[:60]}... #shorts #history"
    description = "A bizarre historical paradox uncovered.\n\nSubscribe to the Shadow Archives for daily mysteries.\n#history #mystery #paradox"

    # TEMPORARILY DISABLED UNTIL CHANNEL IS CREATED
    # upload_video_to_youtube(final_video_path, title, description)
    print("⏳ YouTube upload skipped (waiting for channel creation).")

    print("🎉 Pipeline executed flawlessly. Shutting down runner.")
    
    upload_video_to_youtube(final_video_path, title, description)

    print("🎉 Pipeline executed flawlessly. Shutting down runner.")

if __name__ == "__main__":
    main()
