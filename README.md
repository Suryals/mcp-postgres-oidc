# MCP Postgres OIDC

> ⚠️ **Demo lab — all credentials in this repo are throwaway values** (`alice123`, `mcp_secret`, etc.) for a fully self-contained local environment. No real secrets are committed. Do not deploy as-is.

A portfolio-grade MCP server exposing a PostgreSQL banking database with:

- **Native MCP OAuth** — Keycloak via FastMCP's `KeycloakAuthProvider`: protected-resource metadata, Dynamic Client Registration, browser PKCE login, cached tokens (no token to paste)
- **Role-based column masking** (db_admin / db_analyst / db_readonly)
- **Query guardrails** (SELECT-only, row limits, rate limiting, table ACLs)
- **Full audit trail** (every query logged with user, roles, masked columns)
- **Traefik integration** — exposed at `http://mcp-postgres.traefik.test`

Claude Desktop calling the server live — it ran the OAuth login itself and the
server reports the authenticated role (`db_admin`) and each table's masking policy:

![Claude Desktop listing tables as db_admin](docs/screenshots/claude-desktop-list-tables.png)

---

## Prerequisites

- **Docker** (Docker Desktop or OrbStack)
- Port **80** free (Traefik binds it — override with `TRAEFIK_WEB_PORT` if needed)
- `curl`, `jq` (only for the command-line examples below)

Everything else — Keycloak, the realm, the database, the seed data — is bundled
and starts automatically. No other repos or services required.

---

## Quick Start

### 1. Map the demo hostnames to localhost (one time)

```bash
echo "127.0.0.1 keycloak.test mcp-postgres.traefik.test" | sudo tee -a /etc/hosts
```

> On OrbStack, `*.test` already resolves to localhost — you can skip this.

### 2. Bring up the whole stack

```bash
git clone https://github.com/Suryals/mcp-postgres-oidc.git
cd mcp-postgres-oidc
docker compose up -d --build
```

That single command starts **everything**:

| Service | Role |
|---------|------|
| `mcp-postgres` | PostgreSQL with the banking schema |
| `seeder` | one-shot: loads ~2,000 customers / ~4,000 accounts / ~53,000 transactions, then exits |
| `keycloak` | OIDC provider — **auto-imports** the `mcp-db` realm (3 roles, 3 users) |
| `mcp-server` | the MCP server itself |
| `traefik` (+ `dockerproxy`) | routes `keycloak.test` and `mcp-postgres.traefik.test` |

Give Keycloak ~30s on first boot, then check it's live:

```bash
curl -s http://mcp-postgres.traefik.test/health        # {"status":"ok",...}
curl -s http://keycloak.test/realms/mcp-db | jq .realm  # "mcp-db"
```

> **Port 80 in use?** Run `TRAEFIK_WEB_PORT=8080 docker compose up -d` and use
> `http://mcp-postgres.traefik.test:8080` / `http://keycloak.test:8080` instead.

### 3. See it work

```bash
# Get a token and run the same query as each role — watch the PII masking change
TOKEN=$(./scripts/get_token.sh alice)   # or bob / carol
uv run --with httpx scripts/smoke_test.py
```

---

## Auth Flow (native MCP OAuth)

The server is an OAuth 2.0 resource server. An MCP client needs no pre-shared
token — it discovers everything and runs the browser login itself:

```
Client                                   MCP Server            Keycloak
  │  1. call a tool (no token)                │                    │
  │ ─────────────────────────────────────────▶ 401 +              │
  │                                           │ WWW-Authenticate   │
  │                                           │ resource_metadata  │
  │  2. GET /.well-known/oauth-protected-resource  ───────────────▶│
  │     → authorization_servers: [keycloak/realms/mcp-db]          │
  │  3. GET …/.well-known/openid-configuration ───────────────────▶│
  │  4. Dynamic Client Registration (DCR) ────────────────────────▶│  new client
  │  5. browser: Authorization Code + PKCE  ──────────────────────▶│  user logs in
  │     ◀───────────────────────────────── access token (cached)  │
  │  6. retry tool  Authorization: Bearer <token> ─▶ validate vs   │
  │                                           │      JWKS, extract │
  │                                           │      realm roles   │
  │                                           ▼                    │
  │                       Guardrails (SELECT-only · ACL · LIMIT)   │
  │                                           ▼                    │
  │              PostgreSQL → Column Masking → Audit Log → Response│
```

Steps 1–5 happen once; the cached token is reused silently afterward. Token
validation and all of the OAuth metadata/DCR plumbing are handled by FastMCP's
`KeycloakAuthProvider`.

> 📖 **Full technical readout:** [`docs/AUTH_FLOW.md`](docs/AUTH_FLOW.md) — every
> step (RFC 9728/8414/7591 + PKCE), the Keycloak realm settings that make DCR
> carry roles, the split-horizon network alias, and the problems solved.

---

## Test Users

| User  | Password  | Role         | Can see                          |
|-------|-----------|--------------|----------------------------------|
| alice | alice123  | db_admin     | Everything unmasked + audit_logs |
| bob   | bob123    | db_analyst   | Partial masks on PII             |
| carol | carol123  | db_readonly  | Fully redacted sensitive columns |

### Get a token (password grant — dev only)

```bash
TOKEN=$(curl -s -X POST \
  http://keycloak.test/realms/mcp-db/protocol/openid-connect/token \
  -d 'grant_type=password&client_id=mcp-test&client_secret=mcp-test-secret' \
  -d 'username=alice&password=alice123' \
  | jq -r '.access_token')
```

---

## Use it from Claude

There's **nothing to paste**. The server implements the MCP Authorization spec
(OAuth 2.0 protected-resource metadata + Dynamic Client Registration), so the
client discovers Keycloak on its own, pops a browser login, and caches the token.

**The natural flow:**

```
ask a question → client gets 401 → discovers Keycloak → browser opens
→ log in as alice / bob / carol → token cached → every later query is silent
```

### Claude Desktop

Desktop's **Add custom connector** UI requires an **HTTPS** URL, so this plain-HTTP
local demo connects through the [`mcp-remote`](https://www.npmjs.com/package/mcp-remote)
bridge instead. It runs locally, speaks HTTP to the server, and performs the *same*
OAuth flow (DCR + browser login), caching the token in `~/.mcp-auth` — **no token
in the config**. Merge this into
`~/Library/Application Support/Claude/claude_desktop_config.json`
(full file: `claude_desktop_config.example.json`):

```json
{
  "mcpServers": {
    "banking-db-mcp": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "http://mcp-postgres.traefik.test/mcp", "--allow-http"]
    }
  }
}
```

Requires Node.js (for `npx`). `--allow-http` is needed because the server is HTTP
on a `.test` host. **Fully quit (⌘Q) and reopen Claude Desktop**, then use a tool —
a browser opens to the Keycloak login. Log in as `alice` / `bob` / `carol` (below);
the token is cached. To switch users: quit, `rm -rf ~/.mcp-auth`, reopen.

> If you have a Desktop build whose connector UI accepts HTTP, or you front the
> server with HTTPS (e.g. `mkcert`), you can add the URL directly instead of the bridge.

### Claude Code

```bash
claude mcp add --transport http mcp-postgres-oidc http://mcp-postgres.traefik.test/mcp
# first tool call triggers the browser login, then the token is cached
claude mcp list      # → mcp-postgres-oidc ... ✔ Connected
```

Then ask *"list the tables"* or *"search for customers named Smith"* and watch
the masking change with whoever you logged in as.

### Quick CLI check (no browser)

For scripted testing there's still a password-grant path (the `mcp-test` client):

```bash
./scripts/get_token.sh alice          # alice=admin · bob=analyst · carol=readonly
uv run --with httpx scripts/smoke_test.py
```

---

## Masking Reference

| Column         | db_admin          | db_analyst          | db_readonly        |
|----------------|-------------------|---------------------|--------------------|
| ssn            | `123-45-6789`     | `***-**-6789`       | `***-**-****`      |
| email          | `alice@gmail.com` | `a***@gmail.com`    | `****@*****.***`   |
| phone          | `415-555-1234`    | `***-***-1234`      | `***-***-****`     |
| date_of_birth  | `1985-03-22`      | `1985-**-**`        | `****-**-**`       |
| account_number | `1234567890123456`| `************3456`  | `****************` |
| balance        | `45230.00`        | `45230.00`          | `[REDACTED]`       |
| card_last4     | `4242`            | `4242`              | `****`             |
| salary         | `120000.00`       | `[REDACTED]`        | `[REDACTED]`       |
| national_id    | `123-45-6789`     | `***6789`           | `[REDACTED]`       |
| merchant_raw   | full descriptor   | truncated at 40ch   | `[REDACTED]`       |

---

## MCP Tools

| Tool                     | Min Role    | Description                              |
|--------------------------|-------------|------------------------------------------|
| `list_tables`            | db_readonly | Tables + sensitivity labels              |
| `describe_table`         | db_readonly | Schema + per-column masking policy       |
| `query`                  | db_readonly | Arbitrary SELECT with all guardrails     |
| `search_customers`       | db_readonly | Search by name/email/KYC status          |
| `get_transaction_summary`| db_readonly | Recent transactions (masked)             |
| `get_audit_log`          | db_admin    | Full query audit trail                   |

---

## Architecture

Everything below runs from a single `docker compose up`, on one bridge network
(`mcp-net`):

```
        Client (Claude / curl)
                │  Authorization: Bearer <JWT>
                ▼
   Traefik ──────────────┬─────────────────────────────┐
   (:80)                 │                              │
   keycloak.test ────────┘            mcp-postgres.traefik.test
        │                                    │
        ▼                                    ▼
   Keycloak                       FastMCP KeycloakAuthProvider
   (mcp-db realm,                  (OAuth metadata + DCR proxy;
    auto-imported)  ◄──JWKS/DCR──   validates JWT vs JWKS,
                                    extracts realm roles)
                                             │
                                       FastMCP (streamable-http)
                                       ┌─────┴────────┐
                                  Guardrails      Masking Engine
                                       └─────┬────────┘
                                        asyncpg pool
                                             │
                                       mcp-postgres:5432  (banking_db)
                                             ▲
                                        seeder (one-shot)
```

- `keycloak.test` is aliased to Traefik **inside** the network too, so the MCP
  server and the browser use the **same** Keycloak URL — no split-horizon config,
  and the token issuer matches end to end.
- `dockerproxy` is a tiny nginx shim that lets Traefik talk to Docker daemons
  requiring API ≥ 1.40 (e.g. OrbStack). Traefik is constrained to this compose
  project so it never touches other containers on your host.

---

## Beyond app-layer masking — identity all the way to Postgres

This server's authorization is **application-layer**: one pooled `mcp_user`
connection, masking applied in code by role. The honest weakness is that the
database authorizes nothing per-user — the masking engine is the whole boundary.

[`experiments/pg18-oauth/`](experiments/pg18-oauth/) is a **proven spike** of the
stronger architecture using **PostgreSQL 18's native OAuth**: a Keycloak bearer
authenticates *as the user's role* (`OAUTHBEARER`), Keycloak makes the
authorization decision (UMA), and **Postgres enforces column access natively** —
`db_readonly` gets a hard `permission denied` on `ssn` from the engine, no
`mcp_user`. Includes RFC 8693 token-exchange for the MCP→DB hop. See its
[README](experiments/pg18-oauth/README.md) and
[TARGET_FLOW](experiments/pg18-oauth/TARGET_FLOW.md).
