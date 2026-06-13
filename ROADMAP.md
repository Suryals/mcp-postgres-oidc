# Roadmap

Where this project is, and where it's going. The throughline: make
**principal propagation to the data source** the easy default — no service
accounts — for Python services and MCP servers.

## Done
- [x] **Tier 1 — app-enforced MCP server**: OIDC login (browser PKCE + Dynamic
      Client Registration, nothing to paste), role-based PII masking, per-tool
      RBAC, audit trail attributed to the real user. Clone-and-run via `./up.sh`,
      tested live in Claude Desktop.
- [x] **Tier 2 — identity native to Postgres 18 (proven)**: the user's bearer
      authenticates to Postgres itself via SASL `OAUTHBEARER`; Keycloak makes the
      authorization decision (UMA); Postgres enforces column `GRANT`s. No
      `mcp_user`. RFC 8693 token-exchange for the agent→DB hop. See
      [`experiments/pg18-oauth/mcp_tier2/PROOF.md`](experiments/pg18-oauth/mcp_tier2/PROOF.md).
- [x] **`pgwire.py`** — a minimal token-first `OAUTHBEARER` Postgres wire client,
      the bridge that makes per-user connections possible from Python today.

## In progress / next

### Close the driver gap upstream
Mainstream Python drivers don't speak PG18 `OAUTHBEARER` yet — the one thing
blocking per-user identity-to-Postgres in production. Planned contributions:

- [ ] **Standalone `pg-oauthbearer` package** — extract `pgwire.py` into a small,
      focused open-source library (+ Keycloak/PG18 example) so others hitting this
      gap can `pip install` a token-first client now.
- [ ] **asyncpg** — file a feature request for SASL `OAUTHBEARER` (token-first),
      with `pgwire.py` as a pure-Python proof-of-concept of the message flow; offer
      a PR. (asyncpg implements the protocol itself, so this maps directly.)
- [ ] **psycopg3 ([#1162](https://github.com/psycopg/psycopg/issues/1162))** —
      contribute to the higher-level OAuth API on top of libpq's existing hook;
      share this repo's end-to-end stack (Keycloak Authorization Services + PG18
      validator + token-exchange) as a concrete integration reference.

### Fold Tier 2 into the product
- [ ] Once a driver ships `OAUTHBEARER` (or via the standalone client), make the
      **main MCP server** open per-user connections — retiring the pooled
      `mcp_user` from the running product, not just the spike.
- [ ] Reduce app-layer masking to **cosmetic-only** (partial masks like
      `***-**-6789`) on columns the DB role is already permitted to read.

### Hardening & depth
- [ ] **Row-Level Security (RLS)** policies driven by the propagated identity GUC,
      for per-row (not just per-column) authorization.
- [ ] Audit enrichment: client IP, request/trace id, decision latency.
- [ ] A `Tier 1.5` example: powerless `authenticator` login + `SET LOCAL ROLE`
      (works on any Postgres today, DB-enforced, one inert connecting principal) —
      documented as the pragmatic middle path.

## Known limitations (today)
- Demo lab: synthetic data, locally-generated credentials, not production-hardened.
- Tier 1 still uses a pooled `mcp_user` connection (masking/audit are app-layer).
- Tier 2 is proven over the MCP protocol / a `psql`-class client — not yet wired
  into the browser Claude Desktop flow, pending the driver work above.
