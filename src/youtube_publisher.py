import json
import os

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload


def get_authenticated_service():
    print("🔐 Authenticating with YouTube API...")
    client_secret_env = os.environ.get("YT_CLIENT_SECRET_JSON")
    refresh_token = os.environ.get("YT_REFRESH_TOKEN")
    if not client_secret_env or not refresh_token:
        raise ValueError("Missing YouTube credentials in environment variables.")

    secret = json.loads(client_secret_env)
    client_info = secret.get("web") or secret.get("installed")
    if not client_info:
        raise ValueError("Client secret JSON must contain a 'web' or 'installed' key.")

    credentials = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=client_info["token_uri"],
        client_id=client_info["client_id"],
        client_secret=client_info["client_secret"],
    )
    return build("youtube", "v3", credentials=credentials)


def set_thumbnail(youtube, video_id: str, thumbnail_path: str) -> bool:
    """Custom thumbnails need a verified channel (phone verification). If the
    channel is not verified YouTube returns 403, which must not fail a run whose
    video already uploaded — the auto-selected frame is a survivable outcome."""
    try:
        youtube.thumbnails().set(
            videoId=video_id,
            media_body=MediaFileUpload(thumbnail_path, mimetype="image/jpeg")
        ).execute()
        print(f"🖼️ Custom thumbnail set for {video_id}")
        return True
    except Exception as e:
        print(f"⚠️ Could not set thumbnail ({type(e).__name__}: {e}).")
        print("   Custom thumbnails require a phone-verified channel: "
              "youtube.com/verify")
        return False


def upload_video_to_youtube(video_path: str, title: str, description: str, tags=None,
                            thumbnail_path: str = None):
    youtube = get_authenticated_service()
    print(f"📤 Uploading: {title}")

    # Set PRIVACY_STATUS=unlisted (or private) in the workflow while you're
    # tuning quality — review the output, then flip to public.
    privacy = os.environ.get("PRIVACY_STATUS", "public")

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags or ["history", "shorts", "mystery"],
            "categoryId": "27",  # Education
        },
        "status": {
            "privacyStatus": privacy,
            "madeForKids": False,
            # Honest altered/synthetic content disclosure. Keep this: it's
            # required by YouTube's policy for realistic AI-generated visuals,
            # and disclosed AI content is NOT excluded from monetization.
            # Getting caught *not* disclosing is what gets channels penalized.
            "containsSyntheticMedia": True,
        },
    }

    media = MediaFileUpload(video_path, chunksize=-1, resumable=True,
                            mimetype="video/mp4")
    request = youtube.videos().insert(part="snippet,status", body=body,
                                      media_body=media)
    response = request.execute()
    video_id = response.get("id")
    print(f"✅ Upload complete! https://www.youtube.com/watch?v={video_id}")

    if thumbnail_path and video_id and os.path.exists(thumbnail_path):
        set_thumbnail(youtube, video_id, thumbnail_path)

    return video_id
