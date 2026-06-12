"""
MCP Postgres OIDC Server — entrypoint.

Auth is handled by FastMCP's native Keycloak provider, which implements the full
MCP Authorization spec:

  1. Serves OAuth 2.0 Protected Resource Metadata (/.well-known/oauth-protected-resource)
  2. Answers unauthenticated calls with 401 + WWW-Authenticate pointing at it
  3. Proxies Dynamic Client Registration to Keycloak, so an MCP client (Claude
     Desktop, mcp-remote, the Inspector) can self-register, run the browser
     Authorization-Code + PKCE flow, and cache the resulting token
  4. Validates every bearer token against Keycloak's JWKS

Tools read the caller's identity/roles from the verified token claims
(see auth.identity.get_current_user) and apply guardrails + masking.
"""

import asyncio
import logging
import signal

import asyncpg
import uvicorn
from fastmcp import FastMCP
from fastmcp.server.auth.providers.keycloak import KeycloakAuthProvider
from starlette.requests import Request
from starlette.responses import JSONResponse

from config import settings
from tools.db_tools import register_tools

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)


async def build_app():
    """Create DB pool, register MCP tools, wire up Keycloak OAuth, return ASGI app."""

    logger.info("Connecting to database …")
    pool = await asyncpg.create_pool(
        settings.database_url,
        min_size=2,
        max_size=10,
        command_timeout=settings.query_timeout_seconds + 5,
    )
    logger.info("Database pool ready")

    # Native MCP OAuth: DCR + browser PKCE + JWKS validation, all handled here.
    auth = KeycloakAuthProvider(
        realm_url=f"{settings.keycloak_public_url}/realms/{settings.keycloak_realm}",
        base_url=settings.public_base_url,
    )

    mcp = FastMCP(
        name="MCP Postgres OIDC",
        instructions=(
            "Banking database assistant with role-based access control. "
            "Sensitive columns are automatically masked based on your Keycloak role. "
            "Only SELECT queries are permitted. All queries are audited."
        ),
        auth=auth,
    )

    register_tools(mcp, pool)
    logger.info("MCP tools registered")

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "service": "mcp-postgres-oidc"})

    # http_app() mounts: the /mcp endpoint (auth-protected), the OAuth metadata
    # routes, the DCR proxy, and /health. Its lifespan starts the session manager.
    return mcp.http_app(), pool


async def main() -> None:
    app, pool = await build_app()

    config = uvicorn.Config(
        app,
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        access_log=True,
    )
    server = uvicorn.Server(config)

    loop = asyncio.get_running_loop()

    def _shutdown(*_):
        logger.info("Shutdown signal received …")
        server.should_exit = True

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _shutdown)

    logger.info("MCP Postgres OIDC server starting on http://%s:%s", settings.host, settings.port)
    logger.info("OIDC issuer : %s", settings.keycloak_issuer)
    logger.info("Public URL  : %s", settings.public_base_url)

    try:
        await server.serve()
    finally:
        logger.info("Closing DB pool …")
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
