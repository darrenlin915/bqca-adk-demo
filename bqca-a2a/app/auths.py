"""OAuth 2.0 configuration for both local dev and Gemini Enterprise production.

Pattern adapted from
https://github.com/google/adk-samples/tree/main/python/agents/adk-ae-oauth
"""

from __future__ import annotations

import os

from fastapi.openapi.models import (
    OAuth2,
    OAuthFlowAuthorizationCode,
    OAuthFlows,
)
from google.adk.auth.auth_credential import (
    AuthCredential,
    AuthCredentialTypes,
    OAuth2Auth,
)
from google.adk.auth.auth_tool import AuthConfig

AUTHORIZATION_URL = "https://accounts.google.com/o/oauth2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"

SCOPES = {
    "https://www.googleapis.com/auth/bigquery": (
        "BigQuery API (chat with the Conversational Analytics agent)"
    ),
    "https://www.googleapis.com/auth/presentations": (
        "Google Slides API (create and edit ApexZenith Games analysis presentations)"
    ),
    "https://www.googleapis.com/auth/drive.file": (
        "Drive API (upload chart images created by the agent)"
    ),
}

TOKEN_CACHE_KEY = os.environ.get("AUTH_ID", "bqca-bigquery-auth")

AUTH_SCHEME = OAuth2(
    flows=OAuthFlows(
        authorizationCode=OAuthFlowAuthorizationCode(
            authorizationUrl=AUTHORIZATION_URL,
            tokenUrl=TOKEN_URL,
            scopes=SCOPES,
        )
    )
)

AUTH_CREDENTIAL = AuthCredential(
    auth_type=AuthCredentialTypes.OAUTH2,
    oauth2=OAuth2Auth(
        client_id=os.environ.get("OAUTH_CLIENT_ID", ""),
        client_secret=os.environ.get("OAUTH_CLIENT_SECRET", ""),
    ),
)

AUTH_CONFIG = AuthConfig(
    auth_scheme=AUTH_SCHEME,
    raw_auth_credential=AUTH_CREDENTIAL,
)
