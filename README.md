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

- Docker + OrbStack running
- `auth-lab` stack running (Keycloak at `keycloak.test`, PostgreSQL)
- Traefik running with `traefik-net` external network
- `curl`, `jq` installed

---

## Quick Start

### 1. Start the stack

```bash
cd /Users/surya/ai/projects/mcp-postgres-oidc
docker compose up -d --build
```

### 2. Import the Keycloak realm

```bash
./scripts/import_realm.sh
```

This creates the `mcp-db` realm with 3 roles and 3 test users — no changes to auth-lab.

### 3. Seed the database

```bash
# From the project root
uv run --with faker --with asyncpg scripts/seed.py
```

Or against the Docker container:

```bash
DATABASE_URL=postgresql://mcp_user:mcp_secret@localhost:5432/banking_db \
  uv run --with faker --with asyncpg scripts/seed.py
```

> Inserts ~2,000 customers, ~5,000 accounts, ~100,000 transactions, 200 employees.

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

## Claude Desktop / Claude Code Config

```json
{
  "mcpServers": {
    "mcp-postgres-oidc": {
      "url": "http://mcp-postgres.traefik.test/mcp",
      "headers": {
        "Authorization": "Bearer <your_token>"
      }
    }
  }
}
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

```
Traefik (traefik.test)
  └── mcp-postgres.traefik.test → mcp-server:8000
                                      │
                              OIDCMiddleware
                              (validates JWT vs Keycloak JWKS)
                                      │
                              FastMCP (streamable-http)
                              ┌───────┴────────┐
                         Guardrails        Masking Engine
                              └───────┬────────┘
                                 asyncpg pool
                                      │
                              mcp-postgres:5432
                              (banking_db)
```

Networks:
- `mcp-net` — internal (mcp-server ↔ mcp-postgres)
- `traefik-net` — external (Traefik routing)
- `auth-lab_auth-net` — external (direct reach to auth-keycloak:8080 for JWKS)
