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


def upload_video_to_youtube(video_path: str, title: str, description: str, tags=None):
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
    print(f"✅ Upload complete! https://www.youtube.com/shorts/{response.get('id')}")
