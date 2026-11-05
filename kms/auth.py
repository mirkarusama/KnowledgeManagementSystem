"""Who is asking? Validates Keycloak tokens and performs RFC 8693 token exchange."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import httpx
import jwt

from kms.config import get_settings


@dataclass
class User:
    username: str
    groups: list[str] = field(default_factory=list)
    email: str = ""
    atlassian_id: str = ""
    token: str = ""  # the raw bearer token, forwarded to MCP servers

    @property
    def principals(self) -> list[str]:
        """Tokens matched against the ACL payload stored on every indexed chunk."""
        out = ["authenticated", f"user:{self.username}"]
        if self.atlassian_id:
            out.append(f"user:{self.atlassian_id}")
        out += [f"group:{g}" for g in self.groups]
        return out


class AuthError(Exception):
    pass


_jwks_client: jwt.PyJWKClient | None = None


def _jwks() -> jwt.PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        s = get_settings()
        _jwks_client = jwt.PyJWKClient(f"{s.oidc_internal_url}/protocol/openid-connect/certs", cache_keys=True)
    return _jwks_client


def _clean_groups(raw) -> list[str]:
    # Keycloak's group mapper may emit "/engineering" (full path); keep the leaf name.
    return sorted({g.rstrip("/").split("/")[-1] for g in (raw or []) if g})


def verify_token(token: str, audience: str | None = None) -> User:
    s = get_settings()
    try:
        key = _jwks().get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=audience or s.oidc_audience,
            issuer=s.oidc_issuer,
            options={"require": ["exp", "iat", "sub"]},
        )
    except Exception as e:  # noqa: BLE001 - any failure means "not authenticated"
        raise AuthError(f"invalid token: {e}") from e
    return User(
        username=claims.get("preferred_username") or claims["sub"],
        groups=_clean_groups(claims.get("groups")),
        email=claims.get("email", ""),
        atlassian_id=claims.get("atlassian_id", ""),
        token=token,
    )


def debug_user(header_value: str) -> User:
    """Dev/test only: 'alice;engineering,sre;acc-alice'."""
    parts = header_value.split(";")
    groups = parts[1].split(",") if len(parts) > 1 and parts[1] else []
    return User(username=parts[0], groups=groups, atlassian_id=parts[2] if len(parts) > 2 else "",
                token=f"debug:{header_value}")


def user_from_authorization(authorization: str | None, debug_header: str | None = None,
                            audience: str | None = None) -> User:
    s = get_settings()
    if authorization and authorization.lower().startswith("bearer "):
        tok = authorization.split(" ", 1)[1].strip()
        if tok.startswith("debug:") and s.allow_debug_auth:
            return debug_user(tok[len("debug:"):])
        return verify_token(tok, audience)
    if debug_header and s.allow_debug_auth:
        return debug_user(debug_header)
    raise AuthError("missing bearer token")


# --- RFC 8693 token exchange -------------------------------------------------
_exchange_cache: dict[tuple[str, str], tuple[str, float]] = {}


async def exchange_token(subject_token: str, audience: str) -> str:
    """Swap the user's Keycloak token for one scoped to a source system.

    Analogy: showing your company badge at reception and getting a visitor pass
    that only opens the Confluence door - still in your name.
    """
    s = get_settings()
    if subject_token.startswith("debug:"):
        return subject_token  # dev mode: the mock source understands debug identities
    cache_key = (subject_token[-32:], audience)
    hit = _exchange_cache.get(cache_key)
    if hit and hit[1] > time.time() + 10:
        return hit[0]
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            f"{s.oidc_internal_url}/protocol/openid-connect/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                "subject_token": subject_token,
                "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
                "requested_token_type": "urn:ietf:params:oauth:token-type:access_token",
                "audience": audience,
            },
            auth=(s.mcp_client_id, s.mcp_client_secret),
        )
    if r.status_code != 200:
        raise AuthError(f"token exchange failed: {r.status_code} {r.text[:200]}")
    body = r.json()
    _exchange_cache[cache_key] = (body["access_token"], time.time() + body.get("expires_in", 60))
    return body["access_token"]
