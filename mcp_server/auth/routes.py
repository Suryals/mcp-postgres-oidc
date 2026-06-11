"""
PKCE OAuth 2.0 login routes — no auth required on these endpoints.

GET /auth/login
    Generates PKCE pair, stores verifier, redirects browser to Keycloak.

GET /auth/callback?code=...&state=...
    Keycloak lands here after login.
    Exchanges auth code for tokens using the stored PKCE verifier.
    Returns a page showing the access token (copy/paste into MCP client config).
"""

import logging
import urllib.parse

import httpx
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, JSONResponse
from starlette.routing import Route

from auth.pkce import generate_pkce_pair, create_state, consume_state
from config import settings

logger = logging.getLogger(__name__)

# ── Keycloak endpoint helpers ─────────────────────────────────────────────────

def _auth_endpoint() -> str:
    """Browser-facing Keycloak URL (user's browser must reach this)."""
    return (
        f"http://keycloak.test/realms/{settings.keycloak_realm}"
        "/protocol/openid-connect/auth"
    )

def _token_endpoint() -> str:
    """Internal Keycloak URL (server-to-server token exchange)."""
    return (
        f"{settings.keycloak_internal_url}/realms/{settings.keycloak_realm}"
        "/protocol/openid-connect/token"
    )

def _callback_uri(request: Request) -> str:
    """Absolute callback URL as seen by the browser / Keycloak."""
    return f"http://mcp-postgres.traefik.test/auth/callback"


# ── /auth/login ───────────────────────────────────────────────────────────────

async def login(request: Request) -> RedirectResponse:
    """
    Start the PKCE Authorization Code flow.
    Redirects the browser to Keycloak's login page.
    """
    verifier, challenge = generate_pkce_pair()
    state = create_state(verifier)

    params = {
        "client_id":             settings.pkce_client_id,
        "response_type":         "code",
        "scope":                 "openid profile email",
        "redirect_uri":          _callback_uri(request),
        "state":                 state,
        "code_challenge":        challenge,
        "code_challenge_method": "S256",
    }

    url = _auth_endpoint() + "?" + urllib.parse.urlencode(params)
    logger.info("PKCE login initiated → %s", _auth_endpoint())
    return RedirectResponse(url, status_code=302)


# ── /auth/callback ────────────────────────────────────────────────────────────

async def callback(request: Request) -> HTMLResponse | JSONResponse:
    """
    Keycloak redirects here after successful login.
    Exchanges the auth code for tokens using the PKCE verifier.
    """
    params = dict(request.query_params)

    # Error from Keycloak (user denied, etc.)
    if "error" in params:
        return _error_page(params.get("error"), params.get("error_description", ""))

    code  = params.get("code")
    state = params.get("state")

    if not code or not state:
        return _error_page("missing_params", "code or state parameter missing from callback")

    # Retrieve and consume the PKCE verifier for this state
    verifier = consume_state(state)
    if verifier is None:
        return _error_page("invalid_state", "State not found or expired — please try logging in again")

    # Exchange auth code for tokens (server-to-server, internal Keycloak URL)
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                _token_endpoint(),
                data={
                    "grant_type":    "authorization_code",
                    "client_id":     settings.pkce_client_id,
                    "code":          code,
                    "redirect_uri":  _callback_uri(request),
                    "code_verifier": verifier,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=10,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.error("Token exchange failed: %s — %s", exc.response.status_code, exc.response.text)
            return _error_page("token_exchange_failed", exc.response.text)
        except Exception as exc:
            logger.error("Token exchange error: %s", exc)
            return _error_page("token_exchange_error", str(exc))

    token_data = resp.json()
    access_token  = token_data.get("access_token", "")
    refresh_token = token_data.get("refresh_token", "")
    expires_in    = token_data.get("expires_in", 0)
    token_type    = token_data.get("token_type", "Bearer")

    logger.info("PKCE token exchange successful (expires_in=%ss)", expires_in)

    return _success_page(access_token, refresh_token, expires_in)


# ── HTML responses ────────────────────────────────────────────────────────────

def _success_page(access_token: str, refresh_token: str, expires_in: int) -> HTMLResponse:
    mcp_url = "http://mcp-postgres.traefik.test/mcp"
    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>MCP Postgres — Login Successful</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
           background: #0f172a; color: #e2e8f0; margin: 0; padding: 2rem; }}
    h1  {{ color: #34d399; margin-bottom: 0.25rem; }}
    p   {{ color: #94a3b8; margin-top: 0; }}
    .card {{ background: #1e293b; border-radius: 0.75rem; padding: 1.5rem;
             margin-bottom: 1.5rem; border: 1px solid #334155; }}
    .card h2 {{ color: #7dd3fc; font-size: 0.85rem; text-transform: uppercase;
                letter-spacing: 0.1em; margin: 0 0 0.75rem; }}
    textarea {{ width: 100%; background: #0f172a; color: #a3e635; border: 1px solid #334155;
                border-radius: 0.5rem; padding: 0.75rem; font-family: monospace;
                font-size: 0.8rem; resize: vertical; }}
    button  {{ cursor: pointer; background: #3b82f6; color: white; border: none;
               border-radius: 0.5rem; padding: 0.5rem 1rem; font-size: 0.85rem;
               margin-top: 0.5rem; }}
    button:hover {{ background: #2563eb; }}
    .badge {{ display: inline-block; background: #064e3b; color: #34d399;
              border-radius: 9999px; padding: 0.2rem 0.75rem; font-size: 0.8rem; }}
    pre  {{ background: #0f172a; border: 1px solid #334155; border-radius: 0.5rem;
            padding: 1rem; font-size: 0.8rem; overflow-x: auto; color: #cbd5e1; }}
  </style>
</head>
<body>
  <h1>Login Successful</h1>
  <p>Your access token expires in <span class="badge">{expires_in}s</span>.
     Copy it and paste into your MCP client config.</p>

  <div class="card">
    <h2>Access Token</h2>
    <textarea id="token" rows="6">{access_token}</textarea>
    <button onclick="navigator.clipboard.writeText(document.getElementById('token').value)
                     .then(() => this.textContent = 'Copied!')">
      Copy to clipboard
    </button>
  </div>

  <div class="card">
    <h2>Claude Code / Claude Desktop config</h2>
    <pre>{{
  "mcpServers": {{
    "mcp-postgres-oidc": {{
      "url": "{mcp_url}",
      "headers": {{
        "Authorization": "Bearer &lt;paste token here&gt;"
      }}
    }}
  }}
}}</pre>
  </div>

  <div class="card">
    <h2>Quick test (curl)</h2>
    <pre>curl -s http://mcp-postgres.traefik.test/health</pre>
  </div>
</body>
</html>""")


def _error_page(error: str, description: str) -> HTMLResponse:
    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>MCP Postgres — Auth Error</title>
  <style>
    body {{ font-family: -apple-system, sans-serif; background: #0f172a;
           color: #e2e8f0; padding: 2rem; }}
    h1   {{ color: #f87171; }}
    .card {{ background: #1e293b; border-radius: 0.75rem; padding: 1.5rem;
             border: 1px solid #7f1d1d; }}
    code {{ color: #fca5a5; }}
    a    {{ color: #60a5fa; }}
  </style>
</head>
<body>
  <h1>Authentication Error</h1>
  <div class="card">
    <p><strong>Error:</strong> <code>{error}</code></p>
    <p><strong>Detail:</strong> {description}</p>
  </div>
  <p><a href="/auth/login">Try again</a></p>
</body>
</html>""", status_code=400)


# ── Route list (mounted in server.py) ────────────────────────────────────────
auth_routes = [
    Route("/auth/login",    login),
    Route("/auth/callback", callback),
]
