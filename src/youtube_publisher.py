import json
import os

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# Everything the pipeline does with the channel. Mint a refresh token covering
# all of these with tools/get_youtube_refresh_token.py.
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",     # publishing
    "https://www.googleapis.com/auth/youtube.readonly",   # encore lane's stats
    "https://www.googleapis.com/auth/youtube.force-ssl",  # thumbnails, captions
]


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
        # Declared so the refreshed access token carries read permission as
        # well as upload. This does NOT grant anything the refresh token was
        # not minted with — scopes are fixed at consent time — so a token
        # created for upload only still cannot read the channel. Regenerate it
        # with tools/get_youtube_refresh_token.py when that happens.
        scopes=SCOPES,
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
        # "Accepted", not "set": a 200 here means the bytes were stored. For a
        # regular video that is the same thing as displayed; for a Short it is
        # not, and this log line claiming otherwise is how that went unnoticed.
        print(f"🖼️ Thumbnail accepted for {video_id}")
        return True
    except Exception as e:
        print(f"⚠️ Could not set thumbnail ({type(e).__name__}: {e}).")
        print("   Custom thumbnails require a phone-verified channel: "
              "youtube.com/verify")
        return False


def upload_captions(youtube, video_id: str, srt_path: str,
                    language: str = "en", name: str = "English") -> bool:
    """Attaches the transcript.

    The captions burned into the picture are pixels — YouTube has never had the
    words for any video on this channel. A real track is what makes a video
    eligible for search on its own dialogue, gives the CC button something to
    show, and feeds the auto-translation other languages are surfaced through.

    Costs 400 quota units, by far the most expensive call here after the upload
    itself, which is affordable at one video a day and would not be at ten."""
    if not srt_path or not os.path.exists(srt_path):
        return False
    try:
        youtube.captions().insert(
            part="snippet",
            body={"snippet": {
                "videoId": video_id,
                "language": language,
                "name": name,
                "isDraft": False,
            }},
            media_body=MediaFileUpload(srt_path, mimetype="application/octet-stream"),
        ).execute()
        print(f"🗒️  Caption track uploaded ({language}).")
        return True
    except Exception as e:
        # A token minted before force-ssl was added to SCOPES cannot do this.
        # Not worth failing a run whose video is already public.
        print(f"⚠️ Could not upload captions ({type(e).__name__}: {e}).")
        return False


def ensure_playlist(youtube, title: str, description: str = "") -> str:
    """Returns the id of the channel's playlist with this title, creating it on
    first use. Returns '' if the lookup and the creation both fail.

    Playlists are the only surface that turns one video into a session: a viewer
    who finishes an episode inside a playlist is handed the next one instead of
    being returned to the feed. In 43 days this channel has taken exactly one
    playlist view, because it has no playlists."""
    if not title:
        return ""
    try:
        request = youtube.playlists().list(part="snippet", mine=True,
                                           maxResults=50)
        while request is not None:
            response = request.execute()
            for item in response.get("items", []):
                if item["snippet"]["title"].strip().lower() == title.strip().lower():
                    return item["id"]
            request = youtube.playlists().list_next(request, response)
    except Exception as e:
        print(f"⚠️ Could not list playlists ({type(e).__name__}: {e}).")
        return ""

    try:
        created = youtube.playlists().insert(
            part="snippet,status",
            body={
                "snippet": {"title": title, "description": description},
                "status": {"privacyStatus": "public"},
            },
        ).execute()
        print(f"📂 Created playlist {title!r}.")
        return created["id"]
    except Exception as e:
        print(f"⚠️ Could not create the playlist ({type(e).__name__}: {e}).")
        return ""


def add_to_playlist(youtube, playlist_id: str, video_id: str) -> bool:
    if not playlist_id or not video_id:
        return False
    try:
        youtube.playlistItems().insert(
            part="snippet",
            body={"snippet": {
                "playlistId": playlist_id,
                "resourceId": {"kind": "youtube#video", "videoId": video_id},
            }},
        ).execute()
        print(f"📂 Added to playlist {playlist_id}.")
        return True
    except Exception as e:
        print(f"⚠️ Could not add to the playlist ({type(e).__name__}: {e}).")
        return False


def post_first_comment(youtube, video_id: str, text: str) -> bool:
    """Seeds the comment thread from the channel account.

    50 videos, 9,000 views, zero comments. A video with no comments gives the
    next viewer nothing to reply to, and YouTube reads engagement as a quality
    signal — so the channel has been asking for a conversation it never started.

    Note what this does NOT do: the Data API cannot pin a comment. This posts
    it as the channel, which puts it at the top of a thread of one; pinning is
    a manual click in Studio if you want it held there.
    """
    if not text or not text.strip():
        return False
    try:
        youtube.commentThreads().insert(
            part="snippet",
            body={"snippet": {
                "videoId": video_id,
                "topLevelComment": {"snippet": {"textOriginal": text.strip()[:9000]}},
            }},
        ).execute()
        print(f"💬 First comment posted: {text.strip()[:70]}")
        return True
    except Exception as e:
        # Comments disabled on the video, or a token minted without force-ssl.
        # Neither is worth failing a run whose video is already published.
        print(f"⚠️ Could not post the first comment ({type(e).__name__}: {e}).")
        return False


def upload_video_to_youtube(video_path: str, title: str, description: str, tags=None,
                            thumbnail_path: str = None, first_comment: str = None,
                            caption_path: str = None, playlist_title: str = None,
                            playlist_description: str = "", publish_at: str = None):
    youtube = get_authenticated_service()
    print(f"📤 Uploading: {title}")

    # Set PRIVACY_STATUS=unlisted (or private) in the workflow while you're
    # tuning quality — review the output, then flip to public.
    privacy = os.environ.get("PRIVACY_STATUS", "public")
    # publish_at (RFC 3339, UTC): uploaded private, YouTube makes it public
    # at that time on its own
    if publish_at:
        privacy = "private"

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
            **({"publishAt": publish_at} if publish_at else {}),
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

    if caption_path and video_id:
        upload_captions(youtube, video_id, caption_path)

    if playlist_title and video_id:
        playlist_id = ensure_playlist(youtube, playlist_title,
                                      playlist_description)
        add_to_playlist(youtube, playlist_id, video_id)

    if first_comment and video_id:
        if privacy == "public":
            post_first_comment(youtube, video_id, first_comment)
        else:
            hold_comment(video_id, first_comment)

    return video_id


PENDING_COMMENTS = os.path.join("build", "pending_comments.json")


def hold_comment(video_id: str, text: str):
    """YouTube refuses a comment on a private video (403 "insufficient
    permissions": every upload since 8 Oct, uploaded private). The comment
    waits in build/pending_comments.json; the workflow hands it to the
    thumbnail rotation, which posts it once the video is public."""
    import json
    try:
        with open(PENDING_COMMENTS, encoding="utf-8") as f:
            held = json.load(f)
    except (FileNotFoundError, ValueError):
        held = {}
    held[video_id] = text.strip()
    os.makedirs(os.path.dirname(PENDING_COMMENTS), exist_ok=True)
    with open(PENDING_COMMENTS, "w", encoding="utf-8") as f:
        json.dump(held, f, ensure_ascii=False, indent=2)
    print(f"💬 First comment held until the video is public: {text.strip()[:70]}")
