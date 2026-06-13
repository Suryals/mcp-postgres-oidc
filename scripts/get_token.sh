#!/usr/bin/env bash
# Mint an access token for a test user via the password grant (CLI convenience).
# Credentials come from the generated .env (see ./up.sh).
# Usage: ./scripts/get_token.sh [alice|bob|carol]   (default: alice)
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] || { echo "No .env — run ./up.sh first." >&2; exit 1; }
set -a; . ./.env; set +a

USER="${1:-alice}"
case "$USER" in
  alice) PASS="$ALICE_PASSWORD" ;;
  bob)   PASS="$BOB_PASSWORD" ;;
  carol) PASS="$CAROL_PASSWORD" ;;
  *) echo "Unknown user '$USER' (expected alice|bob|carol)" >&2; exit 1 ;;
esac

curl -s -X POST http://keycloak.test/realms/mcp-db/protocol/openid-connect/token \
  -d "grant_type=password&client_id=mcp-test&client_secret=${MCP_TEST_CLIENT_SECRET}" \
  -d 'scope=openid mcp-roles' \
  -d "username=${USER}&password=${PASS}" | jq -r '.access_token'
