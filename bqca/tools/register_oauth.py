"""Register an OAuth 2.0 Authorization resource with Gemini Enterprise.

Run once before publishing the agent. The Authorization resource tells GE
which OAuth client to use to drive end-user consent and which scope(s) to
request. At runtime, GE injects the user's access token into
tool_context.state["temp:<AUTH_ID>"] for the agent to consume.

Usage:
    uv run python tools/register_oauth.py
"""

from __future__ import annotations

import os
import sys
import urllib.parse

import google.auth
import requests
from dotenv import load_dotenv
from google.auth.transport.requests import Request

load_dotenv()


def main() -> None:
    ge_project = os.environ["GE_PROJECT_ID"]
    auth_id = os.environ["AUTH_ID"]
    client_id = os.environ["OAUTH_CLIENT_ID"]
    client_secret = os.environ["OAUTH_CLIENT_SECRET"]
    scopes = os.environ.get(
        "OAUTH_SCOPES",
        "https://www.googleapis.com/auth/bigquery",
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

    print(f"Registering Authorization '{auth_id}' in project {ge_project}...")
    resp = requests.post(
        f"{base_url}?authorizationId={auth_id}", headers=headers, json=payload
    )
    if resp.status_code == 200:
        print("✅ Created.")
        print(resp.json())
        return
    if resp.status_code == 409:
        print(f"⚠️  '{auth_id}' exists — deleting and recreating...")
        del_resp = requests.delete(f"{base_url}/{auth_id}", headers=headers)
        if del_resp.status_code not in (200, 204):
            print(f"❌ Delete failed: {del_resp.status_code} {del_resp.text}")
            sys.exit(1)
        retry = requests.post(
            f"{base_url}?authorizationId={auth_id}", headers=headers, json=payload
        )
        if retry.status_code == 200:
            print("✅ Recreated.")
            print(retry.json())
            return
        print(f"❌ Recreate failed: {retry.status_code} {retry.text}")
        sys.exit(1)
    print(f"❌ Failed: {resp.status_code} {resp.text}")
    sys.exit(1)


if __name__ == "__main__":
    main()
