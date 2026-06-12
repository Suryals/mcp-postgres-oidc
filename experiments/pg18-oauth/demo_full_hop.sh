#!/usr/bin/env bash
# Full MCP→DB hop with RFC 8693 token-exchange:
#   1. user logs in → token issued to the MCP server's client (mcp-exchanger)
#   2. MCP server EXCHANGES it → token re-audienced for the database (postgres-resource)
#   3. that exchanged token is presented to Postgres 18 via OAUTHBEARER
#   4. Keycloak authorizes the role, Postgres enforces the columns
# Usage: demo_full_hop.sh <user> <pw> <login-role> "<SQL>"
set -euo pipefail
KC=${KC:-https://keycloak.pg.test:8443}
U=$1; P=$2; ROLE=$3; SQL=$4

USERTOK=$(curl -sk -X POST "$KC/realms/pgoauth/protocol/openid-connect/token" \
  -d "grant_type=password&client_id=mcp-exchanger&client_secret=mcp-exchanger-secret&username=$U&password=$P&scope=openid" \
  | jq -r .access_token)

EXTOK=$(curl -sk -X POST "$KC/realms/pgoauth/protocol/openid-connect/token" \
  -d 'grant_type=urn:ietf:params:oauth:grant-type:token-exchange' \
  -d 'client_id=mcp-exchanger' -d 'client_secret=mcp-exchanger-secret' \
  -d "subject_token=$USERTOK" -d 'subject_token_type=urn:ietf:params:oauth:token-type:access_token' \
  -d 'audience=postgres-resource' | jq -r .access_token)

AUD=$(python3 -c "import base64,json,sys;p='$EXTOK'.split('.')[1];p+='='*(-len(p)%4);print(json.loads(base64.urlsafe_b64decode(p)).get('aud'))")
echo "  token-exchange: $U → aud=$AUD (re-audienced for the DB)"
uv run --quiet --with httpx pg_oauth_client.py --token "$EXTOK" "$ROLE" "$SQL" 2>&1 \
  | grep -vE 'InsecureRequestWarning|warnings.warn|verify=False'
