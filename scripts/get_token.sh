#!/usr/bin/env bash
# Mint an access token for a test user (password grant — dev only).
# Usage: ./scripts/get_token.sh [alice|bob|carol]   (default: alice)
set -euo pipefail
USER="${1:-alice}"
case "$USER" in
  alice) PASS=alice123 ;;
  bob)   PASS=bob123 ;;
  carol) PASS=carol123 ;;
  *) echo "Unknown user '$USER' (expected alice|bob|carol)" >&2; exit 1 ;;
esac
curl -s -X POST http://keycloak.test/realms/mcp-db/protocol/openid-connect/token \
  -d 'grant_type=password&client_id=mcp-test&client_secret=mcp-test-secret' \
  -d "username=${USER}&password=${PASS}" | jq -r '.access_token'
