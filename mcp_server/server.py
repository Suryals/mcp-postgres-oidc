"""
MCP Postgres OIDC Server — entrypoint.

Architecture:
  OIDCMiddleware (ASGI)
    └── FastMCP streamable-http app
          └── Tools (list_tables, describe_table, query, search_customers, …)

Auth flow per request:
  1. OIDCMiddleware reads Authorization: Bearer <token>
  2. Validates JWT against Keycloak JWKS (keycloak_internal_url)
  3. Extracts roles + scopes → sets current_user ContextVar
  4. FastMCP routes the MCP call to the appropriate tool
  5. Tool reads current_user → applies guardrails + masking
"""

import asyncio
import logging
import signal
import sys

import asyncpg
import uvicorn
from fastmcp import FastMCP

from auth.oidc import OIDCMiddleware
from auth.routes import auth_routes
from config import settings
from tools.db_tools import register_tools

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)


async def build_app():
    """Create DB pool, register MCP tools, wrap with OIDC middleware."""

    logger.info("Connecting to database …")
    pool = await asyncpg.create_pool(
        settings.database_url,
        min_size=2,
        max_size=10,
        command_timeout=settings.query_timeout_seconds + 5,
    )
    logger.info("Database pool ready")

    mcp = FastMCP(
        name="MCP Postgres OIDC",
        instructions=(
            "Banking database assistant with role-based access control. "
            "Sensitive columns are automatically masked based on your Keycloak role. "
            "Only SELECT queries are permitted. All queries are audited."
        ),
    )

    register_tools(mcp, pool)
    logger.info("MCP tools registered")

    # Build the streamable-HTTP ASGI app (FastMCP v3: http_app, transport "http")
    asgi_app = mcp.http_app()

    # Wrap with OIDC middleware (outermost layer — runs first)
    protected_app = OIDCMiddleware(asgi_app)

    # Unprotected routes: /health + PKCE login flow (/auth/login, /auth/callback)
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "service": "mcp-postgres-oidc"})

    public_app = Starlette(routes=[Route("/health", health)] + auth_routes)

    # Route: public paths bypass OIDC; everything else is protected
    PUBLIC_PATHS = frozenset({"/health", "/auth/login", "/auth/callback"})

    async def root_app(scope, receive, send):
        if scope.get("path") in PUBLIC_PATHS:
            await public_app(scope, receive, send)
        else:
            await protected_app(scope, receive, send)

    return root_app, pool


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

    logger.info(
        "MCP Postgres OIDC server starting on http://%s:%s",
        settings.host, settings.port,
    )
    logger.info("OIDC issuer : %s", settings.keycloak_issuer)
    logger.info("JWKS URI    : %s", settings.jwks_uri)

    try:
        await server.serve()
    finally:
        logger.info("Closing DB pool …")
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
