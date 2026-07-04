import os
import json
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload


def get_authenticated_service():
    """Rebuilds the OAuth credentials using the refresh token stored in GitHub Secrets."""
    print("🔐 Authenticating with YouTube API...")

    client_secret_env = os.environ.get("YT_CLIENT_SECRET_JSON")
    refresh_token = os.environ.get("YT_REFRESH_TOKEN")

    if not client_secret_env or not refresh_token:
        raise ValueError("Missing YouTube credentials in environment variables.")

    secret = json.loads(client_secret_env)
    # FIX: Google client secrets use "web" for web apps but "installed" for
    # desktop-flow apps. Support both so the secret type doesn't matter.
    client_info = secret.get("web") or secret.get("installed")
    if not client_info:
        raise ValueError("Client secret JSON must contain a 'web' or 'installed' key.")

    credentials = Credentials(
        token=None,  # refresh_token will mint a fresh access token
        refresh_token=refresh_token,
        token_uri=client_info["token_uri"],
        client_id=client_info["client_id"],
        client_secret=client_info["client_secret"],
    )

    return build("youtube", "v3", credentials=credentials)


def upload_video_to_youtube(video_path: str, title: str, description: str):
    """Uploads the video file to YouTube as a Short."""
    youtube = get_authenticated_service()

    print(f"📤 Preparing upload for: {title}")

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": ["history", "paradox", "shorts", "mystery", "war", "strategy"],
            "categoryId": "27",  # 27 = Education
        },
        "status": {
            "privacyStatus": "public",  # Set to "private" to review before publishing
            "madeForKids": False,
            # FIX: 'selfDeclaredMadeWithAI' is not a real API field and would be
            # rejected. The actual altered/synthetic-content disclosure field is:
            "containsSyntheticMedia": True,
        },
    }

    media = MediaFileUpload(video_path, chunksize=-1, resumable=True, mimetype="video/mp4")

    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    response = request.execute()
    print(f"✅ Upload Complete! Video ID: {response.get('id')}")
    print(f"🔗 Link: https://www.youtube.com/shorts/{response.get('id')}")
