import json
import os
import re
import shutil

from llm_engine import generate_video_script
from audio_engine import generate_audio_pipeline
from visual_engine import generate_visual_pipeline
from video_engine import generate_video_pipeline
from youtube_publisher import upload_video_to_youtube


def cleanup_build_dir(build_dir="build"):
    if os.path.exists(build_dir):
        shutil.rmtree(build_dir)
    os.makedirs(build_dir)


def sanitize_title(title: str) -> str:
    """YouTube: max 100 chars, no angle brackets. Cut on a word boundary."""
    clean = re.sub(r"[<>]", "", title or "").strip()
    suffix = " #shorts"
    max_body = 100 - len(suffix)
    if len(clean) > max_body:
        clean = clean[:max_body].rsplit(" ", 1)[0].rstrip(".,;:")
    return clean + suffix


def build_metadata(script_data: dict):
    title = sanitize_title(script_data.get("title")
                           or script_data["scenes"][0]["narration"])
    description = script_data.get("description", "").strip()
    description += (
        "\n\nSubscribe to The Shadow Archives for a new dark history story every day."
        "\n\nNote: visuals in this video are AI-generated illustrations of real"
        " historical events.\n#shorts #history #mystery"
    )
    tags = script_data.get("tags") or []
    tags = [t for t in tags if isinstance(t, str)][:15]
    tags += ["history", "shorts", "dark history", "mystery"]
    return title, description, list(dict.fromkeys(tags))  # dedupe, keep order


def main():
    print("🚀 Initiating The Shadow Archives pipeline...")
    build_dir = "build"
    cleanup_build_dir(build_dir)

    # Phase 1: script
    script_data = generate_video_script()
    if not script_data:
        print("❌ Pipeline halted: script generation failed.")
        raise SystemExit(1)

    # Save the script for review/debugging (uploaded as a workflow artifact)
    with open(os.path.join(build_dir, "script.json"), "w", encoding="utf-8") as f:
        json.dump(script_data, f, indent=2, ensure_ascii=False)

    # Phase 2: assets
    script_data = generate_audio_pipeline(script_data, build_dir)
    script_data = generate_visual_pipeline(script_data, build_dir)

    usable = [s for s in script_data["scenes"]
              if s.get("image_path") and s.get("audio_path")]
    if len(usable) < 3:
        print("❌ Pipeline halted: fewer than 3 usable scenes.")
        raise SystemExit(1)

    # Phase 3: render
    final_video_path = generate_video_pipeline(script_data, build_dir)

    # Phase 4: metadata + upload
    title, description, tags = build_metadata(script_data)
    print(f"📋 Title: {title}")

    if os.getenv("SKIP_UPLOAD") == "1":
        print("⏭️ SKIP_UPLOAD=1 — video rendered but not uploaded (dry run).")
    else:
        upload_video_to_youtube(final_video_path, title, description, tags)

    print("🎉 Pipeline complete.")


if __name__ == "__main__":
    main()
