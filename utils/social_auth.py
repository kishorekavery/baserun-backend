"""
Verification of Google / Apple identity tokens for social login.

The identity (email) used to find or create an account must come from a
token the provider signed, never from the request body; otherwise anyone
could log in as any email address.

Env:
  GOOGLE_CLIENT_IDS   comma-separated OAuth client ids accepted as `aud`
  APPLE_CLIENT_IDS    comma-separated bundle/service ids accepted as `aud`
  SOCIAL_LOGIN_ALLOW_UNVERIFIED=true   local development only: trust the
                                       request body (never enable in production)
"""
import asyncio
import os
from dataclasses import dataclass
from typing import List, Optional

import httpx
import jwt

GOOGLE_TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"
APPLE_ISSUER = "https://appleid.apple.com"
APPLE_JWKS_URL = "https://appleid.apple.com/auth/keys"
GOOGLE_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}

_apple_jwks_client: Optional[jwt.PyJWKClient] = None


class SocialAuthError(Exception):
    pass


@dataclass
class SocialIdentity:
    provider: str
    subject: str
    email: Optional[str]
    name: Optional[str]


def _client_ids(env_name: str) -> List[str]:
    return [v.strip() for v in os.getenv(env_name, "").split(",") if v.strip()]


def allow_unverified() -> bool:
    return os.getenv("SOCIAL_LOGIN_ALLOW_UNVERIFIED", "false").strip().lower() in {"1", "true", "yes"}


async def _verify_google(token: str) -> SocialIdentity:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            res = await client.get(GOOGLE_TOKENINFO_URL, params={"id_token": token})
    except httpx.HTTPError as exc:
        raise SocialAuthError("Could not reach Google to verify the token") from exc
    if res.status_code != 200:
        raise SocialAuthError("Invalid Google token")
    info = res.json()
    if info.get("iss") not in GOOGLE_ISSUERS:
        raise SocialAuthError("Invalid Google token issuer")
    allowed = _client_ids("GOOGLE_CLIENT_IDS")
    if allowed and info.get("aud") not in allowed:
        raise SocialAuthError("Google token was issued for a different app")
    if str(info.get("email_verified", "")).lower() != "true":
        raise SocialAuthError("Google account email is not verified")
    return SocialIdentity("google", info["sub"], info.get("email"), info.get("name"))


def _decode_apple(token: str) -> dict:
    global _apple_jwks_client
    if _apple_jwks_client is None:
        _apple_jwks_client = jwt.PyJWKClient(APPLE_JWKS_URL)
    signing_key = _apple_jwks_client.get_signing_key_from_jwt(token)
    allowed = _client_ids("APPLE_CLIENT_IDS")
    return jwt.decode(
        token,
        signing_key.key,
        algorithms=["RS256"],
        issuer=APPLE_ISSUER,
        audience=allowed or None,
        options={"verify_aud": bool(allowed)},
    )


async def _verify_apple(token: str) -> SocialIdentity:
    try:
        claims = await asyncio.to_thread(_decode_apple, token)
    except (jwt.PyJWTError, jwt.PyJWKClientError) as exc:
        raise SocialAuthError("Invalid Apple token") from exc
    return SocialIdentity("apple", claims["sub"], claims.get("email"), None)


async def verify_social_token(provider: str, token: str) -> SocialIdentity:
    if provider == "google":
        return await _verify_google(token)
    if provider == "apple":
        return await _verify_apple(token)
    raise SocialAuthError("Unsupported social provider")
