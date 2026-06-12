# Spike: identity all the way to Postgres (PG18 native OAuth, no service account)

Goal: kill the shared `mcp_user`. Propagate the **end-user's identity** to Postgres
so the **database** authenticates the user and authorizes the request by trusting
Keycloak — `db_readonly` gets a hard `permission denied` on `ssn` *from Postgres*,
not from app-layer masking.

This is feasible as of **PostgreSQL 18**, which adds a native `oauth` auth method
(SASL `OAUTHBEARER`, RFC 7628). Postgres ships the *framework* but no validator —
you load one. We use the CloudNativePG **Keycloak validator** (`kc_validator.so`).

## Architecture

```
Claude/MCP holds user bearer (aud=mcp)
        │  ① RFC 8693 token-exchange at Keycloak → bearer (aud=postgres)
        ▼
   connect to Postgres 18, SASL OAUTHBEARER:
   "n,,\x01auth=Bearer <token>\x01\x01"  + login role = db_readonly
        │
        ▼
   PG18 oauth method → kc_validator.so
        │  ② UMA decision call to Keycloak:
        │     grant_type=uma-ticket & audience=<resource-server>
        │     & permission=appdb#db_readonly & response_mode=decision
        ▼
   Keycloak Authorization Services policies → allow / deny
        │  ③ allow → session authenticated as db_readonly
        ▼
   Postgres runs the query AS db_readonly — column GRANTs enforce.
   SELECT ssn  →  ERROR: permission denied for column ssn
```

No `mcp_user`. The connecting principal *is* the user's role; the IdP made the
authorization decision; the DB enforces column/row access natively.

## What this validator actually does (`kc_validator.so` GUCs)

Pure **UMA delegation** — it does *not* validate the JWT signature locally; it asks
Keycloak to decide. Config (postgresql.conf):

```
oauth_validator_libraries = 'kc_validator'
kc.token_endpoint   = 'https://keycloak.test/realms/mcp-db/protocol/openid-connect/token'
kc.audience         = 'postgres-resource'      # resource-server client (Authz enabled)
kc.resource_name    = 'appdb'                  # permission = <resource_name>#<db_role>
kc.client_id        = 'postgres-resource'
kc.expected_issuer  = 'https://keycloak.test/realms/mcp-db'   # optional iss check
kc.http_timeout_ms  = 2000
```
```
# pg_hba.conf
host all all 0.0.0.0/0 oauth issuer="https://keycloak.test/realms/mcp-db" \
     scope="" validator="kc_validator" delegate_ident_mapping=1
```
The requested **login role** becomes the `<scope>` half of the permission string
(`appdb#db_readonly`), so the DB role name must match a Keycloak scope.

## Status — ✅ PROVEN END TO END

| Step | State |
|------|-------|
| Validator loads in stock `postgres:18` (`LOAD 'kc_validator'`) | ✅ |
| ABI/deps (`libcurl4` + CA trust) | ✅ `Dockerfile` |
| HTTPS Keycloak with an HTTPS issuer | ✅ `docker-compose.yml` + `certs/` |
| Keycloak **Authorization Services** (resource `appdb`, scopes = the 3 roles, role policies, `appdb#<scope>` permissions) | ✅ `setup_authz.sh` |
| UMA decision matrix verified (carol→readonly allow, carol→admin deny, …) | ✅ |
| Postgres roles + **column GRANTs** (readonly: no `ssn`; analyst: no `balance`) | ✅ `pg/init/01_roles_grants.sql` |
| `oauth` `pg_hba` + `delegate_ident_mapping` + validator GUCs | ✅ `pg/pg_hba.conf` + compose `-c kc.*` |
| **Client proof** — Keycloak bearer → `OAUTHBEARER` token-first → login as role | ✅ `pg_oauth_client.py` |

### The proof (live output)

```
carol → db_readonly, SELECT ssn        ⛔ Postgres DENIED (column not granted)
carol → db_readonly, allowed columns   ✓ rows returned
carol → db_admin                       ⛔ Keycloak DENIED the connection (no escalation)
alice → db_admin,    SELECT ssn        ✓ 692-19-1742 (admin sees real PII)
bob   → db_analyst,  ssn ✓ / balance   ✓ ssn  /  ⛔ balance DENIED
```

Two enforcement points, both outside the app: **Keycloak** decides *who may assume
which role* (connection auth); **Postgres** decides *which columns that role reads*
(column GRANTs). No `mcp_user`. App masking is now cosmetic-only.

### Reproduce

```bash
./gen_certs.sh                         # throwaway CA + Keycloak TLS cert
docker compose up -d --build          # HTTPS Keycloak + PG18 + validator
./setup_authz.sh                       # Keycloak Authorization Services
uv run --with httpx pg_oauth_client.py carol carol123 db_readonly "SELECT ssn FROM customers"
#   -> Postgres DENIED the query: permission denied
uv run --with httpx pg_oauth_client.py alice alice123 db_admin    "SELECT ssn FROM customers"
#   -> rows: [['692-19-1742']]
```

### Still open (for the *MCP server* path, not the architecture)
- **RFC 8693 token-exchange** to re-audience the user's MCP token → `aud=postgres`
  before connecting (here the client gets the token directly for clarity).
- **asyncpg/psycopg3 OAUTHBEARER** support so the *production* MCP server connects
  per-user. `pg_oauth_client.py` shows the exact token-first wire format that a
  driver needs — it's ~20 lines.

## The one true blocker for the *MCP server itself*

The MCP server is Python/**asyncpg**, which doesn't speak `OAUTHBEARER` token-first
(neither does psycopg3 yet; pgjdbc is mid-implementation — pgjdbc#3816). The wire
format is trivial —
`SASLInitialResponse(mechanism="OAUTHBEARER", data="n,,\x01auth=Bearer <tok>\x01\x01")`
— but until a Python driver exposes it, the *asyncpg* server can't open per-user
connections. So the spike proves the goal with a **libpq client (psql)**; the
production MCP path waits on driver support (or a libpq-based driver swap).

## Why the two-tier story is the honest answer

- **Tier 1 (ship today, any PG):** `authenticator` login (powerless) + per-request
  `SET LOCAL ROLE` from the token + column GRANTs/RLS. DB enforces, identity flows
  as session GUCs, pooling kept. One inert bootstrap login remains.
- **Tier 2 (this spike, PG18):** no bootstrap login at all — the user's token *is*
  the Postgres credential, IdP decides. Cleanest, but needs PG18 + a validator +
  driver OAUTHBEARER support.

## Reproduce milestone 1

```bash
docker build -t pg18-kcvalidator .
docker run -d --name t -e POSTGRES_PASSWORD=test pg18-kcvalidator
docker exec t psql -U postgres -c "LOAD 'kc_validator';"   # -> LOAD (ABI OK)
```
