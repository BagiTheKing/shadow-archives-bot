import os
import json
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

def get_authenticated_service():
    """Rebuilds the OAuth credentials using the refresh token stored in GitHub Secrets."""
    print("🔐 Authenticating with YouTube API...")
    
    # Load secrets from the environment
    client_secret_env = os.environ.get("YT_CLIENT_SECRET_JSON")
    refresh_token = os.environ.get("YT_REFRESH_TOKEN")
    
    if not client_secret_env or not refresh_token:
        raise ValueError("Missing YouTube credentials in environment variables.")

    # Parse the client secret JSON to extract the required IDs
    client_info = json.loads(client_secret_env)["web"]

    credentials = Credentials(
        token=None,  # We don't need a current token because the refresh_token will generate one
        refresh_token=refresh_token,
        token_uri=client_info["token_uri"],
        client_id=client_info["client_id"],
        client_secret=client_info["client_secret"]
    )
    
    # Build the YouTube API client
    return build("youtube", "v3", credentials=credentials)

def upload_video_to_youtube(video_path: str, title: str, description: str):
    """Uploads the video file to YouTube as a Short."""
    youtube = get_authenticated_service()

    print(f"📤 Preparing upload for: {title}")

    # Define the video metadata
    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": ["history", "paradox", "shorts", "mystery", "war", "strategy"],
            "categoryId": "27"  # 27 = Education
        },
        "status": {
            "privacyStatus": "public",  # Set to "private" if you want to review them first
            "madeForKids": False,
            "selfDeclaredMadeWithAI": True # Highly recommended defensive mechanism
        }
    }

    # Prepare the file stream
    media = MediaFileUpload(video_path, chunksize=-1, resumable=True, mimetype="video/mp4")

    # Execute the upload request
    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media
    )

    response = request.execute()
    print(f"✅ Upload Complete! Video ID: {response.get('id')}")
    print(f"🔗 Link: https://www.youtube.com/shorts/{response.get('id')}")
