# Portfolio page copy — MCP Postgres OIDC

Drop-in content for your portfolio site. Three blocks: a short hook, a "what
it shows" list, and a shot-list telling you exactly which screenshots to grab.

---

## Hook (1–2 sentences, top of the card)

> A production-pattern **Model Context Protocol (MCP) server** that lets an AI
> assistant query a PostgreSQL banking database safely — enforcing **OIDC
> authentication**, **role-based PII masking**, **read-only query guardrails**,
> and a **full audit trail** on every request.

**One-liner (for a project grid / GitHub description):**
> MCP server over PostgreSQL with OIDC auth, role-based PII masking, query guardrails, and audit logging.

---

## What it demonstrates (bullet list)

- **Identity & access** — JWT/RS256 validation against Keycloak JWKS, plus a
  browser-based Authorization Code + PKCE login flow (no client secret).
- **Data protection** — the *same query* returns different data per role:
  admins see full PII, analysts see partial masks, read-only users see fully
  redacted values. Masking happens server-side, after the query.
- **Safe-by-default querying** — SELECT-only enforcement, per-table ACLs,
  automatic `LIMIT` injection, and rate limiting block destructive or
  runaway queries.
- **Auditability** — every query is logged with the user, their roles, and
  which columns were masked.
- **Real infrastructure** — Docker Compose, Traefik routing, asyncpg pooling,
  FastMCP streamable-HTTP transport.

**Stack:** Python · FastMCP · PostgreSQL · Keycloak (OIDC) · asyncpg · Docker · Traefik

---

## Shot-list (capture these for the page)

Aim for 3–4 images. The role-comparison shot is the most important — it makes
the entire project legible in five seconds.

1. **★ Masking comparison (the money shot)** — run the *same* query
   (e.g. `SELECT name, ssn, email, balance FROM customers LIMIT 3`) as
   **alice (admin)** and as **carol (read-only)**, side by side. One shows real
   SSNs/emails/balances; the other shows `***-**-****` / `[REDACTED]`. This
   single image sells the project.
2. **PKCE login result page** — the `/auth/login` flow's final screen showing
   the issued access token + copy button + ready-to-paste MCP config.
3. **Architecture diagram** — redraw the README's ASCII diagram as a clean
   PNG/SVG (Excalidraw works well). Client → OIDC middleware → guardrails →
   masking → Postgres, with the audit-log branch.
4. **Audit log** *(optional)* — `get_audit_log` output as admin, showing logged
   queries with user + masked-columns metadata.

**Tip:** a 10–15s screen-recording GIF of the alice-vs-carol comparison is even
stronger than a still, if your portfolio supports it.

---

## Links block

- **Code:** https://github.com/Suryals/mcp-postgres-oidc
- **Tech:** MCP · OIDC/Keycloak · PostgreSQL · Python · Docker

> Note: this runs on a local Docker + Keycloak + Traefik lab, so there's no
> public live demo — the screenshots and GitHub source tell the story.
