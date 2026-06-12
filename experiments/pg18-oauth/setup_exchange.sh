#!/usr/bin/env bash
# Create the 'mcp-exchanger' client — a confidential client that stands in for the
# MCP server. It holds the user's token (aud=mcp-ish) and uses RFC 8693 Standard
# Token Exchange to mint a token re-audienced for the database (postgres-resource)
# before connecting to Postgres. Run after Keycloak is up.
set -euo pipefail
KC=${KC:-https://keycloak.pg.test:8443}
C() { curl -sk "$@"; }
ADM=$(C -X POST "$KC/realms/master/protocol/openid-connect/token" \
  -d 'grant_type=password&client_id=admin-cli&username=admin&password=admin' | jq -r .access_token)
H=(-H "Authorization: Bearer $ADM" -H 'Content-Type: application/json')

# Delete if it exists (idempotent)
EID=$(C "${H[@]}" "$KC/admin/realms/pgoauth/clients?clientId=mcp-exchanger" | jq -r '.[0].id // empty')
[ -n "$EID" ] && C "${H[@]}" -X DELETE "$KC/admin/realms/pgoauth/clients/$EID" >/dev/null

C "${H[@]}" -X POST "$KC/admin/realms/pgoauth/clients" -d '{
  "clientId": "mcp-exchanger",
  "name": "MCP server (RFC 8693 token-exchange → aud=postgres)",
  "enabled": true,
  "publicClient": false,
  "secret": "mcp-exchanger-secret",
  "serviceAccountsEnabled": true,
  "directAccessGrantsEnabled": true,
  "standardFlowEnabled": false,
  "attributes": { "standard.token.exchange.enabled": "true" }
}' >/dev/null

# Make postgres-resource a requestable audience for the exchange.
EID=$(C "${H[@]}" "$KC/admin/realms/pgoauth/clients?clientId=mcp-exchanger" | jq -r '.[0].id')
C "${H[@]}" -X POST "$KC/admin/realms/pgoauth/clients/$EID/protocol-mappers/models" -d '{
  "name":"aud-postgres-resource","protocol":"openid-connect","protocolMapper":"oidc-audience-mapper",
  "config":{"included.client.audience":"postgres-resource","access.token.claim":"true","id.token.claim":"false"}
}' >/dev/null
echo "mcp-exchanger created (standard token exchange + postgres-resource audience)."
