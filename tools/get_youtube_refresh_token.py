"""
Mint a YouTube refresh token with every scope this pipeline needs.

Why this exists: the token in YT_REFRESH_TOKEN was created with upload
permission only. Uploading works, but the encore lane also has to READ the
channel to find the best-performing Short, and every run has been answering

    HttpError 403 ... "Request had insufficient authentication scopes."

and falling back to the date rotation. Scopes are fixed at the moment a
refresh token is minted, so no code change can add one — the token has to be
created again.

RUN THIS ON YOUR OWN MACHINE, not in CI: it opens a browser for consent.

    pip install google-auth-oauthlib
    python tools/get_youtube_refresh_token.py path/to/client_secret.json

Then copy the printed value into the repository secret YT_REFRESH_TOKEN
(Settings -> Secrets and variables -> Actions). YT_CLIENT_SECRET_JSON stays
as it is.

The token is a credential: it is printed to your terminal only, never written
to disk by this script, and must not be pasted into chat or committed.
"""

import json
import sys

SCOPES = [
    # Publishing. This is the one the current token already has.
    "https://www.googleapis.com/auth/youtube.upload",
    # Reading the channel's own videos and statistics. This is the missing
    # one: it is what the encore lane needs to pick the best Short.
    "https://www.googleapis.com/auth/youtube.readonly",
    # Thumbnails today, and caption-track uploads whenever we add them.
    # Included now so that improvement never costs another re-authorisation.
    "https://www.googleapis.com/auth/youtube.force-ssl",
]


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit("Usage: python tools/get_youtube_refresh_token.py "
                         "<client_secret.json>")

    secret_path = sys.argv[1]
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        raise SystemExit("Missing dependency: pip install google-auth-oauthlib")

    with open(secret_path, encoding="utf-8") as f:
        secret = json.load(f)
    if not (secret.get("installed") or secret.get("web")):
        raise SystemExit("That file is not an OAuth client secret: it needs an "
                         "'installed' or 'web' key. Download it from Google "
                         "Cloud Console -> APIs & Services -> Credentials.")

    print("Requesting these scopes:")
    for scope in SCOPES:
        print(f"  - {scope}")
    print("\nA browser window will open. Sign in with the Google account that "
          "OWNS the channel.\n")

    flow = InstalledAppFlow.from_client_secrets_file(secret_path, SCOPES)
    # access_type=offline plus prompt=consent is what actually returns a
    # refresh token. Without prompt=consent Google reuses the existing grant
    # and returns no refresh token at all, which is the usual reason this
    # procedure appears to silently fail.
    creds = flow.run_local_server(port=0, access_type="offline",
                                  prompt="consent")

    if not creds.refresh_token:
        raise SystemExit(
            "Google returned no refresh token. Revoke this app's access at "
            "https://myaccount.google.com/permissions and run this again.")

    print("\n" + "=" * 64)
    print("Copy this into the GitHub secret YT_REFRESH_TOKEN:\n")
    print(creds.refresh_token)
    print("=" * 64)
    print("\nGranted scopes:", " ".join(creds.scopes or []))
    print("Treat this like a password. Do not commit it or paste it in chat.")


if __name__ == "__main__":
    main()
