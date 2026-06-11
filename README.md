# MCP Postgres OIDC

> ⚠️ **Demo lab — all credentials in this repo are throwaway values** (`alice123`, `mcp_secret`, etc.) for a fully self-contained local environment. No real secrets are committed. Do not deploy as-is.

A portfolio-grade MCP server exposing a PostgreSQL banking database with:

- **OIDC authentication** via Keycloak (JWT/RS256, JWKS validation)
- **Role-based column masking** (db_admin / db_analyst / db_readonly)
- **Query guardrails** (SELECT-only, row limits, rate limiting, table ACLs)
- **Full audit trail** (every query logged with user, roles, masked columns)
- **Traefik integration** — exposed at `http://mcp-postgres.traefik.test`

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

## PKCE Login (Browser Flow)

Open your browser and go to:
```
http://mcp-postgres.traefik.test/auth/login
```

This starts the Authorization Code + PKCE flow:
1. Server generates PKCE challenge, redirects to Keycloak login page
2. You log in as alice / bob / carol
3. Keycloak redirects back to `/auth/callback`
4. Server exchanges the code for a token (using stored PKCE verifier)
5. Page shows your **access token** with a copy button and ready-to-paste MCP config

No client secret needed — `mcp-cli` is a public client with PKCE (`S256`).

---

## Auth Flow

```
Client
  │  1. GET token from Keycloak
  │     POST http://keycloak.test/realms/mcp-db/protocol/openid-connect/token
  │
  │  2. Call MCP tool
  │     Authorization: Bearer <token>
  │
  ▼
OIDCMiddleware
  │  3. Fetch JWKS from auth-keycloak:8080 (internal)
  │  4. Validate RS256 signature + expiry + issuer
  │  5. Extract realm roles → db_admin | db_analyst | db_readonly
  │
  ▼
Guardrails
  │  6. SELECT-only check
  │  7. Table ACL check
  │  8. LIMIT injection
  │
  ▼
PostgreSQL → Column Masking → Audit Log → Response
```

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

The server speaks streamable-HTTP MCP and expects `Authorization: Bearer <token>`.
First mint a token (valid ~1h):

```bash
./scripts/get_token.sh alice    # alice=admin · bob=analyst · carol=readonly
```

### Claude Code

```bash
claude mcp add --transport http mcp-postgres-oidc \
  http://mcp-postgres.traefik.test/mcp \
  --header "Authorization: Bearer $(./scripts/get_token.sh alice)"

claude mcp list      # → mcp-postgres-oidc ... ✔ Connected
```

Then ask Claude things like *"list the tables"* or *"search customers named
Smith"* and watch the masking apply per role.

### Claude Desktop

Desktop bridges remote MCP servers through [`mcp-remote`](https://www.npmjs.com/package/mcp-remote).
Edit `~/Library/Application Support/Claude/claude_desktop_config.json`
(see `claude_desktop_config.example.json` in this repo):

```json
{
  "mcpServers": {
    "mcp-postgres-oidc": {
      "command": "npx",
      "args": [
        "-y", "mcp-remote",
        "http://mcp-postgres.traefik.test/mcp",
        "--header", "Authorization: Bearer ${MCP_TOKEN}"
      ],
      "env": { "MCP_TOKEN": "<paste a token from ./scripts/get_token.sh>" }
    }
  }
}
```

Restart Claude Desktop, then look for **mcp-postgres-oidc** in the tools (🔌) menu.

> Requires Node.js (for `npx`) and the `/etc/hosts` entry from Quick Start so
> Desktop can resolve `mcp-postgres.traefik.test`. Tokens expire — re-run
> `get_token.sh` and update `MCP_TOKEN` when calls start returning 401.

> **Browser login instead of a script:** open
> `http://mcp-postgres.traefik.test/auth/login` for the PKCE flow, which returns
> a token with a copy button (log in as alice / bob / carol).

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
   Keycloak                            OIDCMiddleware
   (mcp-db realm,                      (validates JWT vs Keycloak JWKS,
    auto-imported)  ◄───JWKS (in-net)── extracts realm roles)
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

- The MCP server fetches JWKS **in-network** (`http://keycloak:8080`) but
  validates the token issuer against the **public** URL
  (`http://keycloak.test/realms/mcp-db`) — the classic split-horizon setup.
- `dockerproxy` is a tiny nginx shim that lets Traefik talk to Docker daemons
  requiring API ≥ 1.40 (e.g. OrbStack). Traefik is constrained to this compose
  project so it never touches other containers on your host.
