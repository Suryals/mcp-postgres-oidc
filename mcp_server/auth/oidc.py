"""
OIDC token validation via Keycloak JWKS.

Flow:
  1. Client gets a token from Keycloak (password grant, PKCE, or client credentials)
  2. Client sends:  Authorization: Bearer <token>  with every MCP request
  3. This module validates the token signature, expiry, and issuer
  4. Extracts realm roles and scopes → UserContext stored in a ContextVar
  5. MCP tools read the ContextVar to enforce masking + guardrails
"""

import logging
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Optional

import jwt
from jwt import PyJWKClient, PyJWKClientError

from config import settings

logger = logging.getLogger(__name__)

# ── UserContext ──────────────────────────────────────────────────────────────

@dataclass
class UserContext:
    user_id: str
    email: str
    username: str
    roles: list[str]          # DB roles: db_admin | db_analyst | db_readonly
    scopes: list[str]         # OAuth scopes: db:sensitive, db:admin, …
    raw_claims: dict = field(default_factory=dict, repr=False)

    @property
    def role_tier(self) -> str:
        """Return the highest-privilege DB role this user holds."""
        if "db_admin" in self.roles:
            return "db_admin"
        if "db_analyst" in self.roles:
            return "db_analyst"
        return "db_readonly"

    @property
    def can_access_sensitive(self) -> bool:
        return self.role_tier in ("db_admin", "db_analyst")

    @property
    def can_access_admin(self) -> bool:
        return self.role_tier == "db_admin"


# Per-request context variable — safe for asyncio concurrency
current_user: ContextVar[Optional[UserContext]] = ContextVar("current_user", default=None)


# ── JWKS client (cached, auto-refreshes on key rotation) ────────────────────

_jwks_client: Optional[PyJWKClient] = None


def _get_jwks_client() -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        logger.info("Initialising JWKS client → %s", settings.jwks_uri)
        _jwks_client = PyJWKClient(settings.jwks_uri, cache_jwk_set=True, lifespan=300)
    return _jwks_client


# ── Token validation ─────────────────────────────────────────────────────────

class AuthError(Exception):
    """Raised when a token is missing, malformed, expired, or invalid."""


def validate_token(raw_token: str) -> UserContext:
    """
    Validate a Keycloak-issued JWT and return a UserContext.
    Raises AuthError on any failure.
    """
    try:
        client = _get_jwks_client()
        signing_key = client.get_signing_key_from_jwt(raw_token)
    except PyJWKClientError as exc:
        raise AuthError(f"JWKS fetch/key lookup failed: {exc}") from exc
    except Exception as exc:
        raise AuthError(f"Unexpected error fetching signing key: {exc}") from exc

    try:
        claims = jwt.decode(
            raw_token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=settings.keycloak_issuer,
            # Audience verification is skipped: MCP callers may not set 'aud'.
            # For production, set audience to your client_id and remove this option.
            options={"verify_aud": False},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("Token has expired") from exc
    except jwt.InvalidIssuerError as exc:
        raise AuthError(f"Invalid token issuer (expected {settings.keycloak_issuer})") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthError(f"Token validation failed: {exc}") from exc

    # Extract realm roles (Keycloak puts them in realm_access.roles)
    realm_roles: list[str] = claims.get("realm_access", {}).get("roles", [])
    db_roles = [r for r in realm_roles if r.startswith("db_")]

    # Extract scopes
    scopes = claims.get("scope", "").split()

    return UserContext(
        user_id=claims.get("sub", ""),
        email=claims.get("email", ""),
        username=claims.get("preferred_username", claims.get("sub", "")),
        roles=db_roles,
        scopes=scopes,
        raw_claims=claims,
    )


# ── ASGI middleware ───────────────────────────────────────────────────────────

class OIDCMiddleware:
    """
    Pure ASGI middleware — runs before FastMCP's request handling.

    Every HTTP request must carry:  Authorization: Bearer <token>

    On success: sets current_user ContextVar for the duration of the request.
    On failure: returns 401 JSON immediately.
    """

    SKIP_PATHS = frozenset({"/health"})

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path in self.SKIP_PATHS:
            await self.app(scope, receive, send)
            return

        # Extract Bearer token from headers
        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        auth_bytes: bytes = headers.get(b"authorization", b"")
        auth_header = auth_bytes.decode("latin-1")

        if not auth_header.startswith("Bearer "):
            await _send_401(send, "Missing Authorization header (expected: Bearer <token>)")
            return

        raw_token = auth_header[len("Bearer "):]

        try:
            user = validate_token(raw_token)
        except AuthError as exc:
            logger.warning("Auth rejected [%s]: %s", path, exc)
            await _send_401(send, str(exc))
            return

        logger.debug(
            "Auth OK: user=%s roles=%s path=%s", user.username, user.roles, path
        )

        # Set context var for this request's task
        ctx_token = current_user.set(user)
        try:
            await self.app(scope, receive, send)
        finally:
            current_user.reset(ctx_token)


async def _send_401(send, message: str) -> None:
    import json
    body = json.dumps({"error": "Unauthorized", "detail": message}).encode()
    await send({
        "type": "http.response.start",
        "status": 401,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
            (b"www-authenticate", b'Bearer realm="mcp-db"'),
        ],
    })
    await send({"type": "http.response.body", "body": body})
