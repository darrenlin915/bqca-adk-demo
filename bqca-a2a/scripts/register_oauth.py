"""Register a brand new OAuth 2.0 Authorization resource with Gemini Enterprise.
"""

from __future__ import annotations

import os
import sys
import urllib.parse

import google.auth
import requests
from dotenv import load_dotenv
from google.auth.transport.requests import Request

# Load env from .env in bqca-agent-a2a
load_dotenv()


def main() -> None:
    ge_project = os.environ["GE_PROJECT_ID"]
    # We will use a unique auth_id for Agent Runtime to bypass the async release lock of old ones
    auth_id = "bqca-bigquery-auth-ar"
    client_id = os.environ["OAUTH_CLIENT_ID"]
    client_secret = os.environ["OAUTH_CLIENT_SECRET"]
    scopes = os.environ.get(
        "OAUTH_SCOPES",
        "https://www.googleapis.com/auth/bigquery https://www.googleapis.com/auth/presentations https://www.googleapis.com/auth/drive.file",
    )

    creds, _ = google.auth.default()
    if not creds.valid:
        creds.refresh(Request())

    auth_uri = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": "https://vertexaisearch.cloud.google.com/oauth-redirect",
        "scope": scopes,
        "include_granted_scopes": "true",
        "response_type": "code",
        "access_type": "offline",
        "prompt": "consent",
    })

    base_url = (
        f"https://discoveryengine.googleapis.com/v1alpha/"
        f"projects/{ge_project}/locations/global/authorizations"
    )
    resource_name = (
        f"projects/{ge_project}/locations/global/authorizations/{auth_id}"
    )
    headers = {
        "Authorization": f"Bearer {creds.token}",
        "Content-Type": "application/json",
        "X-Goog-User-Project": ge_project,
    }
    payload = {
        "name": resource_name,
        "serverSideOauth2": {
            "clientId": client_id,
            "clientSecret": client_secret,
            "authorizationUri": auth_uri,
            "tokenUri": "https://oauth2.googleapis.com/token",
        },
    }

    print(f"Registering brand new Authorization '{auth_id}' in project {ge_project}...")
    resp = requests.post(
        f"{base_url}?authorizationId={auth_id}", headers=headers, json=payload
    )
    if resp.status_code == 200:
        print("✅ Created.")
        print(resp.json())
        return
    if resp.status_code == 409:
        print(f"⚠️  '{auth_id}' already exists.")
        return
    print(f"❌ Failed: {resp.status_code} {resp.text}")
    sys.exit(1)


if __name__ == "__main__":
    main()
