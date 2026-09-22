"""
Run this once to authorize the app against your own Gmail account.

It opens a browser window, asks you to sign in and approve read-only
access to your Gmail, then saves the resulting credentials to token.json
so app.py can use them later without you having to log in again (until
the token expires - see README for what happens then, since this app is
running in Google's OAuth "Testing" mode).

Requires credentials.json in this same folder - see README for how to
get that from Google Cloud Console.
"""

import os

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
CREDENTIALS_PATH = os.path.join(os.path.dirname(__file__), "credentials.json")
TOKEN_PATH = os.path.join(os.path.dirname(__file__), "token.json")


def main():
    if not os.path.exists(CREDENTIALS_PATH):
        raise SystemExit(
            "credentials.json not found in the backend folder.\n"
            "Download it from Google Cloud Console (OAuth client ID -> Download JSON)\n"
            "and save it as backend/credentials.json - see README.md."
        )

    flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
    creds = flow.run_local_server(port=0)

    with open(TOKEN_PATH, "w") as f:
        f.write(creds.to_json())

    print(f"Authorization complete. Saved token to {TOKEN_PATH}")


if __name__ == "__main__":
    main()
