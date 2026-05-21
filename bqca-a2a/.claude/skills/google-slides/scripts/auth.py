"""Credential resolution for the google-slides skill.

Tries credentials in this order:
  1. GOOGLE_APPLICATION_CREDENTIALS (service account JSON)
  2. Application Default Credentials (gcloud auth application-default login)
  3. Cached OAuth user token at ~/.config/google-slides-skill/token.json
  4. Interactive OAuth flow if client_secret.json exists at ~/.config/google-slides-skill/

The first source that produces credentials with the required scopes wins.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

DEFAULT_SCOPES = [
    "https://www.googleapis.com/auth/presentations",
    "https://www.googleapis.com/auth/drive.file",
]

CONFIG_DIR = Path.home() / ".config" / "google-slides-skill"
TOKEN_PATH = CONFIG_DIR / "token.json"
CLIENT_SECRET_PATH = CONFIG_DIR / "client_secret.json"


def _try_service_account(scopes):
    sa_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not sa_path:
        return None
    try:
        from google.oauth2 import service_account
    except ImportError:
        return None
    try:
        return service_account.Credentials.from_service_account_file(sa_path, scopes=scopes)
    except Exception as e:
        print(f"[auth] GOOGLE_APPLICATION_CREDENTIALS set but failed to load: {e}", file=sys.stderr)
        return None


def _try_adc(scopes):
    try:
        from google.auth import default
    except ImportError:
        return None
    try:
        creds, _ = default(scopes=scopes)
        # ADC may return creds without the requested scopes if they were granted
        # for a different set. The Slides API will reject — caller should verify.
        return creds
    except Exception:
        return None


def _try_cached_oauth(scopes):
    if not TOKEN_PATH.exists():
        return None
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
    except ImportError:
        return None
    creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), scopes)
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            TOKEN_PATH.write_text(creds.to_json())
        except Exception as e:
            print(f"[auth] failed to refresh cached token: {e}", file=sys.stderr)
            return None
    return creds


def _try_oauth_flow(scopes):
    if not CLIENT_SECRET_PATH.exists():
        return None
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        return None
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET_PATH), scopes)
    creds = flow.run_local_server(port=0)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(creds.to_json())
    return creds


def get_credentials(scopes=None):
    """Return Google API credentials, trying the supported sources in order.

    Raises RuntimeError with a helpful message if none work.
    """
    scopes = scopes or DEFAULT_SCOPES
    for fn in (_try_service_account, _try_adc, _try_cached_oauth, _try_oauth_flow):
        creds = fn(scopes)
        if creds is not None:
            return creds

    raise RuntimeError(
        "No usable Google credentials found. Set up one of:\n"
        "  1. gcloud auth application-default login \\\n"
        "       --scopes=\"https://www.googleapis.com/auth/presentations,https://www.googleapis.com/auth/drive.file,openid,https://www.googleapis.com/auth/userinfo.email\"\n"
        "  2. export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json\n"
        f"  3. Place an OAuth client_secret.json at {CLIENT_SECRET_PATH}\n"
        "See SKILL.md → Authentication for details."
    )


if __name__ == "__main__":
    creds = get_credentials()
    src = creds.__class__.__module__
    print(f"✓ credentials resolved from: {src}")
