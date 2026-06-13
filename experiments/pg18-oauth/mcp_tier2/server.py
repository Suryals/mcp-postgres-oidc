"""
Tier-2 MCP server — NO shared service account.

Every tool call:
  1. takes the session user's validated bearer (FastMCP JWTVerifier),
  2. RFC 8693 token-exchange → a token re-audienced for Postgres,
  3. opens a per-request Postgres 18 connection via OAUTHBEARER, logging in AS the
     user's role (db_admin/db_analyst/db_readonly),
  4. Postgres (with the kc_validator + column GRANTs) authenticates + authorizes +
     audits the query AS that user.

There is no mcp_user, no connection pool, no app-layer masking. The database is the
authority. This is the compliance goal: identity propagated to the source, the
query runs as the user who initiated the session.
"""
import os
import httpx
from fastmcp import FastMCP
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.server.dependencies import get_access_token

import pgwire

KC_PUBLIC = "https://keycloak.pg.test:8443/realms/pgoauth"
KC_TOKEN = f"{KC_PUBLIC}/protocol/openid-connect/token"
JWKS = "http://localhost:8080/realms/pgoauth/protocol/openid-connect/certs"  # same keys, no TLS hassle
PG_HOST, PG_PORT = "localhost", 55432
EXCHANGER_ID = "mcp-exchanger"
EXCHANGER_SECRET = os.environ.get("EXCHANGER_SECRET", "mcp-exchanger-secret")

# Front-door auth: validate the session user's JWT (signature, issuer).
auth = JWTVerifier(jwks_uri=JWKS, issuer=KC_PUBLIC)
mcp = FastMCP(name="Tier2 Banking DB (no service account)", auth=auth)


def _role_from_claims(claims: dict) -> str:
    roles = (claims.get("realm_access") or {}).get("roles", [])
    for r in ("db_admin", "db_analyst", "db_readonly"):
        if r in roles:
            return r
    raise PermissionError("token carries no db_* role")


def _exchange_for_db(user_token: str) -> str:
    """RFC 8693: re-audience the user's token for Postgres (postgres-resource)."""
    r = httpx.post(KC_TOKEN, verify=False, data={
        "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
        "client_id": EXCHANGER_ID, "client_secret": EXCHANGER_SECRET,
        "subject_token": user_token,
        "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
        "audience": "postgres-resource",
    })
    r.raise_for_status()
    return r.json()["access_token"]


def _run_as_user(sql: str) -> dict:
    tok = get_access_token()                      # the session user's validated token
    role = _role_from_claims(tok.claims)
    user = tok.claims.get("preferred_username", "?")
    db_token = _exchange_for_db(tok.token)        # → aud=postgres
    try:
        cols, rows = pgwire.run_select(PG_HOST, PG_PORT, role, db_token, sql)
        return {"connected_as": role, "session_user": user, "columns": cols, "rows": rows}
    except pgwire.PgDenied as e:
        return {"connected_as": role, "session_user": user, "denied": str(e)}


@mcp.tool
def whoami() -> dict:
    """Who Postgres thinks you are — proves the session runs as the real user, not a shared account."""
    return _run_as_user(
        "SELECT current_user AS postgres_session_user, "
        "current_setting('application_name') AS app"
    )


@mcp.tool
def query(sql: str) -> dict:
    """Run a SELECT against the banking DB AS the session user's role (Postgres enforces)."""
    return _run_as_user(sql)


if __name__ == "__main__":
    mcp.run(transport="http", host="127.0.0.1", port=8009)
