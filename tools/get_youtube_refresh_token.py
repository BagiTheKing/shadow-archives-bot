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

On Windows use `py` instead of `python`.

Then copy the printed value into the repository secret YT_REFRESH_TOKEN
(Settings -> Secrets and variables -> Actions). YT_CLIENT_SECRET_JSON stays
as it is, and it must: the refresh token is bound to the client that minted
it, so replacing one without the other breaks publishing.

WEB CLIENTS AND "Error 400: redirect_uri_mismatch"
--------------------------------------------------
A "Desktop app" OAuth client accepts any loopback port, so the flow can pick
a free one. A "Web application" client only accepts redirect URIs registered
against it in the Cloud Console, and this pipeline's client is a web client,
so the first attempt failed with redirect_uri_mismatch.

This script therefore uses a FIXED port for web clients and prints the exact
URI to register. Add it under
Cloud Console -> APIs & Services -> Credentials -> your OAuth client ->
Authorised redirect URIs, save, wait a few seconds, and run this again.

The token is a credential: it is printed to your terminal only, never written
to disk by this script, and must not be pasted into chat or committed.
"""

import argparse
import json
import sys
from urllib.parse import urlparse

# Any registered loopback port works; 8080 is the conventional one.
DEFAULT_PORT = 8080

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


def _loopback_ports(client_info):
    """Ports of any localhost redirect URIs already registered on the client."""
    ports = []
    for uri in client_info.get("redirect_uris") or []:
        parsed = urlparse(uri)
        if parsed.hostname in ("localhost", "127.0.0.1"):
            ports.append(parsed.port or (443 if parsed.scheme == "https" else 80))
    return ports


def main():
    parser = argparse.ArgumentParser(
        description="Mint a YouTube refresh token with all required scopes.")
    parser.add_argument("client_secret", help="Path to the OAuth client JSON")
    parser.add_argument("--port", type=int, default=None,
                        help=f"Loopback port for the consent redirect "
                             f"(default: a registered one, else {DEFAULT_PORT})")
    args = parser.parse_args()

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        raise SystemExit("Missing dependency: pip install google-auth-oauthlib")

    with open(args.client_secret, encoding="utf-8") as f:
        secret = json.load(f)
    client_info = secret.get("installed") or secret.get("web")
    if not client_info:
        raise SystemExit("That file is not an OAuth client secret: it needs an "
                         "'installed' or 'web' key. Download it from Google "
                         "Cloud Console -> APIs & Services -> Credentials.")

    is_web = "web" in secret
    if args.port is not None:
        port = args.port
    elif is_web:
        # A web client will only accept a redirect URI registered against it,
        # so reuse one it already has rather than picking a random port and
        # earning "Error 400: redirect_uri_mismatch".
        registered = _loopback_ports(client_info)
        port = registered[0] if registered else DEFAULT_PORT
    else:
        port = 0                      # desktop clients accept any free port

    print(f"Client type: {'Web application' if is_web else 'Desktop app'}")
    print("Requesting these scopes:")
    for scope in SCOPES:
        print(f"  - {scope}")

    if is_web:
        print(f"\nThis redirect URI must be registered on the client:\n")
        print(f"    http://localhost:{port}/\n")
        print("If consent fails with 'Error 400: redirect_uri_mismatch', add "
              "exactly that URI under Cloud Console -> APIs & Services -> "
              "Credentials -> your OAuth client -> Authorised redirect URIs, "
              "save, wait a few seconds, and run this again.")

    print("\nA browser window will open. Sign in with the Google account that "
          "OWNS the channel.\n")

    flow = InstalledAppFlow.from_client_secrets_file(args.client_secret, SCOPES)
    # access_type=offline plus prompt=consent is what actually returns a
    # refresh token. Without prompt=consent Google reuses the existing grant
    # and returns no refresh token at all, which is the usual reason this
    # procedure appears to silently fail.
    try:
        creds = flow.run_local_server(port=port, access_type="offline",
                                      prompt="consent")
    except OSError as e:
        raise SystemExit(f"Could not listen on port {port} ({e}). "
                         f"Close whatever is using it, or pass --port with a "
                         f"different registered port.")

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
