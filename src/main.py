import os
import re
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


def build_title(first_scene_text: str) -> str:
    """
    YouTube titles: max 100 chars, no '<' or '>' allowed.
    Cut at a word boundary so titles don't end mid-word.
    """
    clean = re.sub(r"[<>]", "", first_scene_text).strip()
    suffix = " #shorts #history"
    max_body = 100 - len(suffix)
    if len(clean) > max_body:
        clean = clean[:max_body].rsplit(" ", 1)[0].rstrip(".,;:") + "..."
    return clean + suffix


def main():
    print("🚀 Initiating The Shadow Archives Automated Pipeline...")
    build_dir = "build"
    cleanup_build_dir(build_dir)

    # Phase 1: Script & Logic
    script_data = generate_video_script()
    if not script_data:
        print("❌ Pipeline halted: Script generation failed.")
        raise SystemExit(1)  # non-zero exit so the Actions run is marked failed

    # Phase 2: Asset Generation
    script_data = generate_audio_pipeline(script_data, build_dir)
    script_data = generate_visual_pipeline(script_data, build_dir)

    # Guard: if every scene lost its image or audio, don't render a broken video
    usable = [s for s in script_data["scenes"] if s.get("image_path") and s.get("audio_path")]
    if not usable:
        print("❌ Pipeline halted: No usable scenes after asset generation.")
        raise SystemExit(1)

    # Phase 3: Video Rendering
    final_video_path = generate_video_pipeline(script_data, build_dir)

    # Phase 4: Metadata & Upload
    title = build_title(script_data["scenes"][0]["narration"])
    description = (
        "A bizarre historical paradox uncovered.\n\n"
        "Subscribe to the Shadow Archives for daily mysteries.\n"
        "#history #mystery #paradox"
    )

    upload_video_to_youtube(final_video_path, title, description)

    print("🎉 Pipeline executed flawlessly. Shutting down runner.")


if __name__ == "__main__":
    main()
