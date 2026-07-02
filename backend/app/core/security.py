"""JWT validation. Two modes selected by settings.auth_mode:

- "local": HS256 tokens signed with a dev-only shared secret. Used for all
  local development (Stages 1-12 of the dev plan) — no Cognito account needed.
- "cognito": RS256 tokens validated against the real Cognito JWKS endpoint.
  Switched on at Stage 13 (real AWS cutover) via config only — no code change
  in any module that depends on `get_current_claims`.

Both modes return the same claim shape so nothing downstream (permissions,
RLS context) needs to know which mode is active.
"""

import asyncio
import time
from functools import lru_cache
from typing import TypedDict

import httpx
from jose import JWTError, jwt

from app.config import get_settings
from app.core.exceptions import PermissionError_

settings = get_settings()
_jwks_lock = asyncio.Lock()


class TokenClaims(TypedDict):
    sub: str
    role: str


def create_local_token(sub: str, expires_in_seconds: int = 3600) -> str:
    """Dev-only helper for issuing test tokens shaped like a real Cognito token.
    Role is deliberately NOT embedded here — AuthContextMiddleware always
    resolves role from the `profiles` table by `sub`, the same as it would
    for a real Cognito token, so local dev exercises the real lookup path.
    Never used when auth_mode == 'cognito'."""
    payload = {
        "sub": sub,
        "iat": int(time.time()),
        "exp": int(time.time()) + expires_in_seconds,
    }
    return jwt.encode(payload, settings.local_jwt_secret, algorithm="HS256")


@lru_cache
def _jwks_client_cache_key() -> str:
    return f"{settings.cognito_region}:{settings.cognito_user_pool_id}"


_jwks_cache: dict[str, tuple[float, list[dict]]] = {}
_JWKS_TTL_SECONDS = 3600


async def _fetch_cognito_jwks() -> list[dict]:
    """Async on purpose — this used to call httpx.get() (blocking) from inside
    an async request path (AuthContextMiddleware.dispatch -> verify_token).
    That stalled the ENTIRE uvicorn worker's event loop on every cache-miss
    fetch, not just the one request. Fixed as part of the architecture review
    (P0 finding #1) before it could bite in production under load."""
    key = _jwks_client_cache_key()
    cached = _jwks_cache.get(key)
    if cached and (time.time() - cached[0]) < _JWKS_TTL_SECONDS:
        return cached[1]

    async with _jwks_lock:
        # Re-check after acquiring the lock — another concurrent request may
        # have already refreshed it while we were waiting.
        cached = _jwks_cache.get(key)
        if cached and (time.time() - cached[0]) < _JWKS_TTL_SECONDS:
            return cached[1]

        url = (
            f"https://cognito-idp.{settings.cognito_region}.amazonaws.com/"
            f"{settings.cognito_user_pool_id}/.well-known/jwks.json"
        )
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url)
        resp.raise_for_status()
        keys = resp.json()["keys"]
        _jwks_cache[key] = (time.time(), keys)
        return keys


async def verify_token(token: str) -> TokenClaims:
    try:
        if settings.auth_mode == "local":
            payload = jwt.decode(token, settings.local_jwt_secret, algorithms=["HS256"])
        else:
            keys = await _fetch_cognito_jwks()
            payload = jwt.decode(
                token,
                keys,
                algorithms=["RS256"],
                audience=settings.cognito_app_client_id,
            )
    except JWTError as exc:
        raise PermissionError_("Invalid or expired token", code="INVALID_TOKEN") from exc

    groups = payload.get("cognito:groups") or []
    role = groups[0] if groups else payload.get("role", "")
    return TokenClaims(sub=payload["sub"], role=role)
