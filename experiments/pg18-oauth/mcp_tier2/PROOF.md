# Proof — MCP with no shared service account: identity propagated to the source

**Goal:** prove that *without a shared user*, an MCP server can pass the session
user's identity to the data source, which **authenticates, authorizes, and runs
the query as the user who initiated the session** — the requirement for compliance
(real AuthN, per-user AuthZ, and audit against the actual principal).

This is the live MCP server (`server.py`) — driven over the MCP protocol exactly
as Claude would — backed by PostgreSQL 18 native OAuth. **There is no `mcp_user`,
no connection pool, and no app-layer masking.** The database is the authority.

## The chain (per request, no shared account)

```
session user's bearer (validated by the MCP server, JWTVerifier)
   → RFC 8693 token-exchange  → token re-audienced for Postgres
   → connect to PG18 via SASL OAUTHBEARER, login role = the user's realm role
   → kc_validator asks Keycloak (UMA) "may this user be <role>?"  → allow/deny
   → Postgres runs the query AS that role; column GRANTs enforce; audit at source
```

## Result (driven via the MCP protocol)

| user | `whoami` → `current_user` in Postgres | `SELECT ssn` |
|------|---------------------------------------|--------------|
| alice  | **`db_admin`**    | ✅ `692-19-1742` (real value) |
| carol  | **`db_readonly`** | ⛔ `permission denied for table customers` (from Postgres) |
| carol  | `db_readonly`     | ✅ `SELECT id, first_name, email` succeeds (column-level, not total deny) |

The decisive line: **`current_user` is the real role, not a shared account.** In the
Tier-1 app-layer server, `current_user` is always `mcp_user`; here it is `db_admin`
/ `db_readonly` — the session runs *as the user*, and Postgres (not the app) decides
what they may read. The only login roles that exist are the three user roles:

```
$ psql -c "SELECT rolname FROM pg_roles WHERE rolcanlogin AND rolname LIKE 'db_%'"
 db_admin
 db_analyst
 db_readonly         -- no mcp_user
```

## Why this is the compliance answer

- **AuthN at the source** — Postgres authenticates the real user (via the IdP), not
  a stand-in account. "Who connected?" → the user.
- **AuthZ at the source** — the user's role + column GRANTs are enforced by the
  database engine. A bug in the app cannot grant access the role doesn't have.
- **Audit at the source** — every connection/query is attributable to the user's
  role at the DB level, independent of application logging.

## Run it

```bash
# Tier-2 stack already up (../docker-compose.yml + ../setup_authz.sh + ../setup_exchange.sh)
cd experiments/pg18-oauth/mcp_tier2
uv run --with fastmcp --with httpx --with 'pyjwt[crypto]' python server.py &   # MCP server, no service account
uv run --with httpx python drive.py alice alice123 whoami                       # -> current_user = db_admin
uv run --with httpx python drive.py carol carol123 query "SELECT ssn FROM customers"  # -> permission denied
```

## The remaining real-world gap (honest note)

The session user's front-door token here is obtained via direct grant for clarity.
The browser-DCR path (Tier 1) and this source-enforced path both work; uniting them
in one production server needs a mainstream Python driver that speaks `OAUTHBEARER`
(asyncpg/psycopg3 don't yet) — `pgwire.py` is the ~120-line stand-in proving the
exact wire protocol that such a driver would use.
