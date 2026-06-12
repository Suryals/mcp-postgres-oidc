# Target flow — identity all the way to Postgres (no service account)

The end state: the **user's identity reaches Postgres**, the **IdP (Keycloak) makes
the authorization decision**, and **Postgres enforces** column/row access natively.
No shared `mcp_user`. App-layer masking drops to cosmetic-only.

---

## As-is (today, on `main`) — app is the security boundary

```
 alice/bob/carol ──JWT(role)──▶ MCP server ──▶ [ mcp_user ]  ← one god account
                                    │                │  full SELECT on everything
                                    │                ▼
                                    │            Postgres returns RAW rows
                                    └─ masking engine redacts in code, by role
                                         ▲
                                         └── a bug here = data leak (sole boundary)
```
DB authorizes nothing per-user. The token gates the app, not the data.

---

## To-be (target, PG18) — the database is the security boundary

```
┌──────────┐   OIDC login (browser PKCE)         ┌───────────────────────┐
│  alice   │ ──────────────────────────────────▶ │      Keycloak         │
│ (browser)│ ◀── bearer  aud=mcp, roles=[…] ───── │  (Authorization       │
└──────────┘                                      │   Services / policies)│
     │ Bearer aud=mcp                             └───────────┬───────────┘
     ▼                                                        │ ▲
┌─────────────────┐                                           │ │
│   MCP server    │  ① RFC 8693 TOKEN EXCHANGE                │ │
│ (FastMCP)       │ ──── exchange user token ─────────────────┘ │
│                 │ ◀─── bearer  aud=postgres, roles=[…] ────────┘
│                 │
│  per request:   │  ② connect to PG18 — SASL OAUTHBEARER, token-first:
│                 │     "n,,\x01auth=Bearer <aud=postgres tok>\x01\x01"
│                 │     login role = db_readonly   (from token's role claim)
└────────┬────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│  PostgreSQL 18      pg_hba: ... oauth issuer=…/mcp-db          │
│                              validator="kc_validator"         │
│                              delegate_ident_mapping=1         │
│   ┌──────────────────────────────────────────────────────┐  │
│   │ kc_validator.so   ③ UMA decision call to Keycloak:    │  │
│   │   grant_type=uma-ticket                                │  │
│   │   audience=<resource-server>                           │  │
│   │   permission = appdb#db_readonly   ◀── resource#role   │──┼──▶ Keycloak
│   │   response_mode=decision                               │  │   policies
│   └──────────────────────────────────────────────────────┘  │   decide
│                          │ ④ ALLOW                            │ ◀──── allow/deny
│                          ▼                                    │
│   session authenticated AS  db_readonly  (a real PG role)     │
│                          │                                    │
│                          ▼   ⑤ query runs as db_readonly      │
│   SELECT first_name, ssn FROM customers                       │
│        first_name ✓                                           │
│        ssn        ✗  ERROR: permission denied for column ssn  │  ← DB enforces
│                                                               │   (column GRANTs)
└─────────────────────────────────────────────────────────────┘
   • no mcp_user anywhere
   • app masking = cosmetic only (***-**-6789) on columns the role CAN read
   • audit: SET LOCAL app.username carries the human for who-saw-what
```

---

## Trust boundaries (what each layer is responsible for)

| Layer | Decides | Enforced by |
|-------|---------|-------------|
| Keycloak | *Who you are* + *may this user act as `db_readonly` on `appdb`?* | IdP auth + Authorization Services policies |
| Token exchange (RFC 8693) | re-audiences the token for the DB, downscopes | Keycloak |
| PG18 `oauth` + `kc_validator` | accept the connection only if the IdP says allow | Postgres auth, delegated to Keycloak |
| Postgres roles + column GRANTs / RLS | *what rows/columns this role can read at all* | Postgres engine (`permission denied`) |
| App masking engine | cosmetic partial masks on permitted columns | MCP server (no longer the boundary) |

## The one gap (today)
② needs the client to speak `OAUTHBEARER` token-first. `psql`/libpq can; asyncpg/
psycopg3 can't yet (pgjdbc#3816 tracks the JDBC side). So the **live proof runs via
`psql`**; the asyncpg MCP server adopts it once a Python driver exposes the token
hook (or via a libpq-based driver). Wire format is trivial and already known.

## Step legend
1. **Token exchange** — MCP server swaps the user's `aud=mcp` token for an
   `aud=postgres` token (still carrying the user + roles). Never hands the DB the
   MCP-audience token.
2. **Connect** — token-first OAUTHBEARER; the login role names the privilege wanted.
3. **Delegate** — `kc_validator` asks Keycloak to decide `appdb#<role>` for this token.
4. **Decision** — Keycloak policies allow/deny; PG accepts/rejects the connection.
5. **Enforce** — the session *is* `db_readonly`; Postgres column GRANTs deny `ssn`
   before a row is produced — independent of any app code.
