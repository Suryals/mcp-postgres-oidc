#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# Import the mcp-db Keycloak realm via Admin REST API
# No changes to the auth-lab project needed.
#
# Usage: ./scripts/import_realm.sh
# Requires: curl, jq
# ──────────────────────────────────────────────────────────────
set -euo pipefail

KEYCLOAK_URL="${KEYCLOAK_URL:-http://keycloak.test}"
ADMIN_USER="${KEYCLOAK_ADMIN_USER:-admin}"
ADMIN_PASS="${KEYCLOAK_ADMIN_PASS:-admin}"
REALM_FILE="$(dirname "$0")/../keycloak/mcp-db-realm.json"

echo "→ Authenticating with Keycloak at ${KEYCLOAK_URL} ..."

TOKEN=$(curl -sf \
  -X POST "${KEYCLOAK_URL}/realms/master/protocol/openid-connect/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=password" \
  -d "client_id=admin-cli" \
  -d "username=${ADMIN_USER}" \
  -d "password=${ADMIN_PASS}" \
  | jq -r '.access_token')

if [ -z "$TOKEN" ] || [ "$TOKEN" = "null" ]; then
  echo "✗ Failed to get admin token. Check KEYCLOAK_URL, KEYCLOAK_ADMIN_USER, KEYCLOAK_ADMIN_PASS"
  exit 1
fi

echo "→ Checking if realm 'mcp-db' already exists ..."
HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
  -H "Authorization: Bearer ${TOKEN}" \
  "${KEYCLOAK_URL}/admin/realms/mcp-db")

if [ "$HTTP_STATUS" = "200" ]; then
  echo "⚠ Realm 'mcp-db' already exists. Deleting and re-importing ..."
  curl -sf -X DELETE \
    -H "Authorization: Bearer ${TOKEN}" \
    "${KEYCLOAK_URL}/admin/realms/mcp-db"
  echo "  Deleted."
fi

echo "→ Importing realm from ${REALM_FILE} ..."
curl -sf \
  -X POST "${KEYCLOAK_URL}/admin/realms" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  --data-binary "@${REALM_FILE}"

echo ""
echo "✓ Realm 'mcp-db' imported successfully."
echo ""
echo "Test users:"
echo "  alice / alice123  → db_admin"
echo "  bob   / bob123    → db_analyst"
echo "  carol / carol123  → db_readonly"
echo ""
echo "To get a token:"
echo "  curl -s -X POST http://keycloak.test/realms/mcp-db/protocol/openid-connect/token \\"
echo "    -d 'grant_type=password&client_id=mcp-test&client_secret=mcp-test-secret' \\"
echo "    -d 'username=alice&password=alice123' | jq -r '.access_token'"
