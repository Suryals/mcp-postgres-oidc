# Auth Flow — Technical Readout

How this server gives an MCP client (Claude Desktop, Claude Code, `mcp-remote`,
the MCP Inspector) a **zero-paste OAuth login**: the client discovers that auth
is required, registers itself, opens a browser login, caches the token, and
reuses it silently — then the server validates that token and applies
role-based masking.

This document explains every moving part, why each one is needed, and the
non-obvious problems that had to be solved.

---

## 0. TL;DR

```
ask a question → 401 → discover Keycloak → register (DCR) → browser PKCE login
→ token cached → every later call is silent → server validates JWT → mask by role
```

- **Spec:** [MCP Authorization](https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization),
  which builds on OAuth 2.1 + RFC 9728 (Protected Resource Metadata) + RFC 7591
  (Dynamic Client Registration) + RFC 8414 (AS Metadata) + PKCE (RFC 7636).
- **Server side:** FastMCP's `KeycloakAuthProvider` does the OAuth plumbing;
  our tools just read the verified claims.
- **IdP:** Keycloak 26.6 (DCR became MCP-compatible in 26.6).

---

## 1. The problem with "just check the bearer token"

The original server had a hand-rolled ASGI middleware that did exactly one thing:

```
if no valid "Authorization: Bearer <jwt>" header → 401
```

That works only if the client *already has* a token. It gives the client no way
to **obtain** one. There's no "where do I log in?" signal, so a fresh client just
gets 401 and stops. The whole point of MCP auth is that the **401 itself carries
the directions** to authenticate, and the client follows them automatically.

So the fix isn't "validate better" — it's "advertise the authorization server
and support the OAuth handshake." That's what the rest of this document is.

---

## 2. The actors

| Actor | Role in OAuth terms | Here |
|-------|--------------------|------|
| Claude / `mcp-remote` / Inspector | OAuth **client** | runs the flow, caches the token |
| MCP server (`mcp-server`) | **Resource Server** | advertises metadata, validates tokens, serves tools |
| Keycloak (`mcp-keycloak`) | **Authorization Server** (IdP) | login UI, issues tokens, JWKS, DCR endpoint |
| Postgres (`mcp-postgres`) | protected resource | the banking data being masked |

The MCP server is **not** the authorization server — it *points at* Keycloak.
This separation is what lets any standards-compliant IdP slot in.

---

## 3. The discovery + login sequence

Everything below is what happens on the **first** tool call. Steps 1–6 run once;
afterward the client has a cached token and jumps straight to step 7.

```
Client                         MCP Server (RS)                 Keycloak (AS)
  │                                  │                              │
  │ 1. POST /mcp  (no token)         │                              │
  │ ────────────────────────────────▶                              │
  │                                  │                              │
  │ 2. 401 Unauthorized              │                              │
  │    WWW-Authenticate: Bearer …    │                              │
  │      resource_metadata=          │                              │
  │      ".../.well-known/oauth-     │                              │
  │       protected-resource/mcp"    │                              │
  │ ◀────────────────────────────────                              │
  │                                  │                              │
  │ 3. GET that resource-metadata URL│                              │
  │ ────────────────────────────────▶                              │
  │    { resource, authorization_servers:[ keycloak/realms/mcp-db ]}│
  │ ◀────────────────────────────────                              │
  │                                  │                              │
  │ 4. GET {AS}/.well-known/openid-configuration ─────────────────▶│
  │    { authorization_endpoint, token_endpoint,                   │
  │      registration_endpoint, jwks_uri }                         │
  │ ◀──────────────────────────────────────────────────────────────│
  │                                  │                              │
  │ 5. POST registration_endpoint   (Dynamic Client Registration)  │
  │    { redirect_uris:[http://localhost:PORT/...], … } ──────────▶│
  │    → { client_id }   (a brand-new public client)               │
  │ ◀──────────────────────────────────────────────────────────────│
  │                                  │                              │
  │ 6. Browser opens authorization_endpoint  (Auth Code + PKCE)    │
  │    user logs in as alice/bob/carol ───────────────────────────▶│
  │    ◀── redirect ?code=… ; client exchanges code+verifier ─────▶│
  │    ◀── access_token (JWT, cached locally) ─────────────────────│
  │                                  │                              │
  │ 7. POST /mcp  Authorization: Bearer <JWT>                      │
  │ ────────────────────────────────▶ validate vs JWKS,            │
  │                                  │ extract realm roles,         │
  │                                  │ guardrails + masking         │
  │ ◀──────────────── tool result (masked per role) ───────────────│
```

### What each step actually returns (verified with curl)

**Step 2 — the 401 challenge** (the thing the old middleware couldn't produce):

```
HTTP/1.1 401 Unauthorized
WWW-Authenticate: Bearer error="invalid_token",
  error_description="…",
  resource_metadata="http://mcp-postgres.traefik.test/.well-known/oauth-protected-resource/mcp"
```

**Step 3 — Protected Resource Metadata** (RFC 9728), served by the MCP server:

```json
{
  "resource": "http://mcp-postgres.traefik.test/mcp",
  "authorization_servers": ["http://keycloak.test/realms/mcp-db"],
  "scopes_supported": ["openid"],
  "bearer_methods_supported": ["header"]
}
```

**Step 4 — Authorization Server Metadata** (RFC 8414), served by Keycloak:

```json
{
  "issuer": "http://keycloak.test/realms/mcp-db",
  "authorization_endpoint": "http://keycloak.test/realms/mcp-db/protocol/openid-connect/auth",
  "token_endpoint":         "http://keycloak.test/realms/mcp-db/protocol/openid-connect/token",
  "registration_endpoint":  "http://keycloak.test/realms/mcp-db/clients-registrations/openid-connect",
  "jwks_uri":               "http://keycloak.test/realms/mcp-db/protocol/openid-connect/certs"
}
```

**Step 5 — Dynamic Client Registration** (RFC 7591), anonymous POST to Keycloak:

```jsonc
// request
{ "client_name": "claude", "redirect_uris": ["http://localhost:9876/callback"],
  "grant_types": ["authorization_code"], "response_types": ["code"],
  "token_endpoint_auth_method": "none" }
// response
{ "client_id": "ca9dfcbe-…", "redirect_uris": ["http://localhost:9876/callback"], … }
```

**Step 6 — token** (decoded), the payload our masking depends on:

```json
{ "iss": "http://keycloak.test/realms/mcp-db",
  "preferred_username": "alice",
  "realm_access": { "roles": ["db_admin"] } }
```

---

## 4. Server side — `KeycloakAuthProvider`

All of the OAuth behavior above is one object. `mcp_server/server.py`:

```python
from fastmcp.server.auth.providers.keycloak import KeycloakAuthProvider

auth = KeycloakAuthProvider(
    realm_url=f"{settings.keycloak_public_url}/realms/{settings.keycloak_realm}",
    base_url=settings.public_base_url,
)
mcp = FastMCP(name="MCP Postgres OIDC", instructions="…", auth=auth)

app = mcp.http_app()   # mounts /mcp (protected), the .well-known metadata,
                       # the DCR proxy, the 401 challenge, and /health
```

`mcp.http_app()` is a Starlette app whose lifespan also starts the streamable-HTTP
session manager. Passing `auth=` makes FastMCP:

1. serve `/.well-known/oauth-protected-resource[/mcp]` (step 3),
2. answer unauthenticated `/mcp` with the `WWW-Authenticate` 401 (step 2),
3. proxy DCR and the OAuth endpoints toward Keycloak (steps 4–6),
4. verify every incoming JWT against Keycloak's JWKS (step 7).

There is **no custom auth middleware anymore** — `oidc.py`, `routes.py`, and
`pkce.py` were deleted.

### Reading identity in a tool

After FastMCP has verified the token, a tool reads the caller's claims through a
dependency (`mcp_server/auth/identity.py`):

```python
from fastmcp.server.dependencies import get_access_token

def get_current_user() -> UserContext:
    token = get_access_token()                 # already validated by FastMCP
    claims = token.claims or {}
    realm_roles = (claims.get("realm_access") or {}).get("roles", [])
    db_roles = [r for r in realm_roles if r.startswith("db_")]
    return UserContext(
        user_id=claims.get("sub", ""),
        email=claims.get("email", ""),
        username=claims.get("preferred_username", ""),
        roles=db_roles, scopes=(claims.get("scope") or "").split(),
        raw_claims=claims,
    )
```

`UserContext.role_tier` collapses the realm roles to the highest of
`db_admin > db_analyst > db_readonly`, which the masking engine uses to decide
how much of each PII column to reveal.

---

## 5. Keycloak realm — the three things that make DCR work

A dynamically-registered client is a *different* client from our static ones, so
it only gets whatever the realm hands every new client by default. Three realm
settings (`keycloak/mcp-db-realm.json`) make DCR tokens usable:

### 5.1 A default scope that emits roles + identity

DCR clients inherit the realm's **default** client scopes. We define one,
`mcp-roles`, list it in `defaultDefaultClientScopes`, and put the realm-role +
username + email mappers inside it:

```jsonc
"clientScopes": [{
  "name": "mcp-roles",
  "protocol": "openid-connect",
  "protocolMappers": [
    { "name": "realm roles", "protocolMapper": "oidc-usermodel-realm-role-mapper",
      "config": { "claim.name": "realm_access.roles", "multivalued": "true",
                  "access.token.claim": "true", "jsonType.label": "String" } },
    { "name": "username", "protocolMapper": "oidc-usermodel-property-mapper",
      "config": { "user.attribute": "username", "claim.name": "preferred_username",
                  "access.token.claim": "true" } },
    { "name": "email", "protocolMapper": "oidc-usermodel-property-mapper",
      "config": { "user.attribute": "email", "claim.name": "email",
                  "access.token.claim": "true" } }
  ]
}],
"defaultDefaultClientScopes": ["mcp-roles"],
```

Without this, the access token has **no** `realm_access.roles` and every user
silently collapses to the most-restrictive (readonly) mask.

### 5.2 Relaxed Dynamic-Client-Registration policy

Keycloak gates anonymous DCR with client-registration policies. We replace the
defaults with a single Trusted-Hosts policy that allows the redirect hosts MCP
clients use:

```jsonc
"components": {
  "org.keycloak.services.clientregistration.policy.ClientRegistrationPolicy": [{
    "name": "Trusted Hosts", "providerId": "trusted-hosts", "subType": "anonymous",
    "config": {
      "host-sending-registration-request-must-match": ["false"],
      "client-uris-must-match": ["true"],
      "trusted-hosts": ["localhost", "127.0.0.1", "claude.ai", "claude.com"]
    }
  }]
}
```

`localhost`/`127.0.0.1` cover Claude Desktop, `mcp-remote`, and the Inspector
(they register a loopback callback); `claude.ai`/`claude.com` cover the web client.
Omitting the other default policies (notably *Full Scope Disabled*) is deliberate
— see 5.3.

### 5.3 Full scope, so realm roles aren't filtered out

With Keycloak's default *Full Scope Disabled* registration policy, a DCR client
is created with `fullScopeAllowed=false`, which **filters `realm_access.roles`
down to the client's own role mappings** — i.e. empty. By not applying that
policy, DCR clients get `fullScopeAllowed=true` and the user's realm roles pass
through. (The static `mcp-test` client sets `"fullScopeAllowed": true` explicitly
for the same reason.)

---

## 6. One Keycloak URL for both server and browser (no split-horizon)

The token's `iss` is whatever Keycloak's `KC_HOSTNAME` says (`keycloak.test`), and
the **same** URL is used in two places that live on different sides of the network:

- the **browser** resolves `keycloak.test` via `/etc/hosts` → `127.0.0.1` → Traefik,
- the **MCP server**, inside Docker, also needs to fetch `keycloak.test`'s metadata
  and JWKS.

`KeycloakAuthProvider` takes a single `realm_url`, so both must agree. The fix is a
Docker **network alias** on Traefik (`docker-compose.yml`):

```yaml
traefik:
  networks:
    mcp-net:
      aliases: [keycloak.test, mcp-postgres.traefik.test]
```

Now `http://keycloak.test/...` resolves **inside** the network to Traefik, which
routes by `Host: keycloak.test` to the Keycloak container. The server and the
browser use the identical URL, and the `iss` claim matches end to end — no
internal-vs-external URL juggling.

---

## 7. Request path once authenticated (step 7)

```
Bearer JWT
  → KeycloakAuthProvider: verify signature (JWKS), issuer, expiry
  → get_current_user(): realm_access.roles → db_* roles → UserContext
  → guardrails (query_guard.py): SELECT-only · table ACL · LIMIT injection
  → SQL on asyncpg pool
  → masking engine (engine.py): per-column policy by role_tier
  → audit log row (user, roles, masked columns, query hash)
  → JSON result
```

Same query, three identities:

| column | alice (db_admin) | bob (db_analyst) | carol (db_readonly) |
|--------|------------------|------------------|---------------------|
| ssn    | `627-30-2075`    | `***-**-2075`    | `***-**-****`       |
| email  | `lauren18@example.com` | `l***@example.com` | `****@*****.***` |

---

## 8. Problems solved along the way

| Symptom | Root cause | Fix |
|---------|-----------|-----|
| Client gets 401 and gives up | bearer-only middleware advertised no AS | `KeycloakAuthProvider` + protected-resource metadata |
| Keycloak 26.2 DCR incompatible with MCP clients | pre-26.6 DCR bug | bump image to `26.6` |
| DCR token has no roles → everyone readonly | DCR clients only get realm default scopes | `mcp-roles` **default** scope with the realm-role mapper |
| DCR token *still* drops realm roles | default *Full Scope Disabled* policy filters them | omit that policy → `fullScopeAllowed=true` |
| Keycloak 26.6 refuses to boot | disabling both DCR host checks is illegal | enable `client-uris-must-match`, trust localhost/claude.ai |
| Server can't fetch `keycloak.test` metadata | hostname only resolved on the host, not in-container | Traefik network **alias** for `keycloak.test` |
| `iss` mismatch between server and browser | split internal/external URLs | single `realm_url`, same alias |

---

## 9. Try it yourself

```bash
# 1. metadata the client discovers
curl -s http://mcp-postgres.traefik.test/.well-known/oauth-protected-resource/mcp | jq

# 2. the 401 challenge that kicks off the flow
curl -i http://mcp-postgres.traefik.test/mcp \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | grep -i www-authenticate

# 3. full browser-equivalent flow (DCR → PKCE → token → masked query)
uv run --with httpx scripts/smoke_test.py
```

In a real client there's nothing to run — add the URL
`http://mcp-postgres.traefik.test/mcp` as a Claude Desktop custom connector and the
browser login appears on first use. See the **Use it from Claude** section of the
[README](../README.md).
