# Handoff — build the portfolio page for *Principal Propagation (MCP)*

You are building a portfolio page for this project. This brief is self-contained:
everything you need (story, assets, talking points, honest limits) is here. Read
the repo `README.md` and `experiments/pg18-oauth/mcp_tier2/PROOF.md` for depth.

---

## One-line pitch
> A reference pattern (and working demo) for letting an AI agent query a regulated
> database **as the end user** — no shared service account — with authentication,
> authorization, and audit enforced **at the data source**, for compliance.

## Repo & status
- **GitHub:** https://github.com/Suryals/principal-propagation-mcp (public)
- **State:** working, tested **live in Claude Desktop**; clone-and-run via `./up.sh`.
- **Stack:** FastMCP · Keycloak 26.6 (OIDC) · PostgreSQL (16 demo / 18 for Tier 2) ·
  Traefik · Docker. Python.

## The narrative arc (use this as the page's spine)
1. **The problem.** Agents/apps reach databases through one shared "service
   account" with full access. That breaks compliance three ways: the DB never sees
   the real user (no AuthN), the account can read everything (coarse AuthZ), and the
   audit trail records the service account, not the person (fake audit).
2. **The pattern.** *Principal propagation* — carry the end user's verified identity
   all the way to the source, and enforce AuthN + AuthZ + audit where the data lives.
3. **The proof.** Two tiers, both built and demonstrated:
   - **Tier 1 (runs today, any Postgres):** an MCP server with OIDC login (browser
     PKCE + Dynamic Client Registration — *nothing to paste*), **role-based PII
     masking**, per-tool RBAC, and an **audit trail attributed to the real user**.
     This is what runs from Claude Desktop.
   - **Tier 2 (the compliance end-state, proven):** the user's identity reaches
     **Postgres 18 itself** via SASL `OAUTHBEARER` — **no service account at all**.
     Postgres authenticates the user (trusting Keycloak), authorizes via column
     `GRANT`s, and `SELECT ssn` as `db_readonly` is **denied by the database
     engine**, not app code.

## The three "wow" moments (lead with these)
1. **Same query, different principal.** The identical prompt in Claude Desktop:
   admin sees `ssn = 537-47-1781`; readonly sees `***-**-****`. One image tells the
   whole story.
2. **No service account — `current_user` is the real user.** In Tier 2, `whoami`
   returns `db_admin` / `db_readonly` (not a shared `mcp_user`), and the DB itself
   refuses unauthorized columns. This is the differentiator most projects don't have.
3. **Audit at the source.** The audit log resolves every PII access to the real
   human (`alice@banking.demo`) with role + masked columns — the literal SOC 2 /
   PCI / GDPR "who accessed this?" answer.

## Technical highlights worth calling out (credibility for a security/eng audience)
- **Native MCP OAuth** end to end: protected-resource metadata → DCR → browser PKCE
  → cached token, validated per request. No pasted secrets.
- **⭐ The wire client** (`experiments/pg18-oauth/mcp_tier2/pgwire.py`): asyncpg and
  psycopg3 can't speak `OAUTHBEARER` yet, so this ~120-line Postgres-wire client is
  what makes per-user DB connections possible *today*. The token-first handshake is
  the crux. Great "I went down to the protocol" detail.
- **RFC 8693 token-exchange** to re-audience the user's token for the database.
- **Keycloak Authorization Services (UMA)** + PG18 validator: the IdP makes the
  authz decision; Postgres trusts it.
- **Clone-and-run with generated credentials** — `./up.sh` mints random passwords
  locally at bring-up (nothing sensitive committed).

## Assets in the repo (use directly)
- `docs/screenshots/claude-desktop-list-tables.png` — admin, unmasked, role reported
- `docs/screenshots/claude-desktop-masked-readonly.png` — readonly, PII redacted
- `docs/screenshots/claude-desktop-audit-denied.png` — tool RBAC denial
- `docs/examples/audit-log.md` — real audit trail (renders as a table)
- `docs/AUTH_FLOW.md` — full OAuth/DCR/PKCE walkthrough (diagram)
- `experiments/pg18-oauth/TARGET_FLOW.md` — as-is vs to-be architecture diagram
- `experiments/pg18-oauth/mcp_tier2/PROOF.md` — the no-service-account proof

## Suggested page structure
1. Hero: pitch + the admin-vs-readonly screenshot side-by-side.
2. "The problem with service accounts" (3 bullets).
3. "The pattern" — a simple diagram (AuthN/AuthZ/Audit at the source).
4. "See it work" — the masking before/after + audit-log screenshots.
5. "How far it goes" — Tier 2: identity native to Postgres, the wire-client detail.
6. Tech stack chips + **View on GitHub** button.
7. Short "what I learned / why it matters for compliance" closer.

## Tone & audience
Technical hiring managers, security/platform engineers. Confident but precise.
This project's edge is *correctness and depth*, not flash — let the architecture
and the live proof speak.

## Honesty guardrails (do NOT overstate — it'll cost credibility)
- It's a **demo lab**: synthetic Faker data, generated local credentials, not
  hardened for production deployment. Say so plainly.
- **Tier 1 still uses a pooled `mcp_user`** DB connection — masking/audit are
  app-layer there. Don't claim Tier 1 has "no service account."
- **Tier 2 has no service account and is proven**, but via the MCP protocol / a
  `psql`-class client — *not yet* wired into the browser Claude Desktop flow,
  because mainstream Python drivers lack `OAUTHBEARER`. Frame it as "proven
  end-state, pending driver support," which is true and still impressive.
- No public live demo URL (needs local Docker + Keycloak + Traefik) — the page
  relies on screenshots + GitHub, not a hosted link.

## No-live-demo note
Everything runs locally (Docker). The page should use the screenshots and link to
the repo; do not promise a clickable hosted demo.
