#!/usr/bin/env bash
# Print the demo logins generated for this environment (from .env).
# Run anytime after ./up.sh.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] || { echo "No .env yet — run ./up.sh first." >&2; exit 1; }
. ./.env
cat <<EOF
Demo logins (generated locally by ./up.sh):

  alice  / ${ALICE_PASSWORD}   → db_admin     (sees everything)
  bob    / ${BOB_PASSWORD}   → db_analyst   (partial PII masking)
  carol  / ${CAROL_PASSWORD}   → db_readonly  (PII fully redacted)

MCP endpoint: http://mcp-postgres.traefik.test/mcp
CLI token:    ./scripts/get_token.sh alice
EOF
