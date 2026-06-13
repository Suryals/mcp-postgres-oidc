#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Clone-and-run bring-up.
#
#   ./up.sh
#
# On first run it GENERATES a local .env with random credentials (nothing
# sensitive is ever committed), renders the Keycloak realm from a template,
# starts the stack, and prints the demo logins. Re-runs reuse the same .env.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail
cd "$(dirname "$0")"

ENV_FILE=".env"
TEMPLATE="keycloak/realm.template.json"
RENDERED="keycloak/import/mcp-db-realm.json"

rand() { LC_ALL=C tr -dc 'A-Za-z0-9' < /dev/urandom | head -c 20; }

# 1. Generate credentials once, locally.
if [ ! -f "$ENV_FILE" ]; then
  echo "→ First run: generating local credentials in $ENV_FILE …"
  cat > "$ENV_FILE" <<EOF
# Generated locally by up.sh on first run. Git-ignored. Safe to delete to rotate.
ALICE_PASSWORD=$(rand)
BOB_PASSWORD=$(rand)
CAROL_PASSWORD=$(rand)
MCP_TEST_CLIENT_SECRET=$(rand)
POSTGRES_PASSWORD=$(rand)
EOF
fi
set -a; . "./$ENV_FILE"; set +a

# 2. Render the realm (inject generated passwords) into the import dir.
mkdir -p keycloak/import
envsubst '${ALICE_PASSWORD} ${BOB_PASSWORD} ${CAROL_PASSWORD} ${MCP_TEST_CLIENT_SECRET}' \
  < "$TEMPLATE" > "$RENDERED"
echo "→ Rendered realm with generated credentials."

# 3. /etc/hosts hint (skipped on OrbStack, which resolves *.test automatically).
if ! grep -q "mcp-postgres.traefik.test" /etc/hosts 2>/dev/null \
   && ! python3 -c "import socket;socket.gethostbyname('mcp-postgres.traefik.test')" 2>/dev/null; then
  echo "⚠  Add the demo hostnames to /etc/hosts (one time):"
  echo '     echo "127.0.0.1 keycloak.test mcp-postgres.traefik.test" | sudo tee -a /etc/hosts'
fi

# 4. Start everything.
echo "→ Building and starting the stack …"
docker compose up -d --build

# 5. Wait for readiness.
printf "→ Waiting for Keycloak realm "
until [ "$(curl -s -m 3 -o /dev/null -w '%{http_code}' http://keycloak.test/realms/mcp-db/.well-known/openid-configuration 2>/dev/null)" = "200" ]; do
  printf "."; sleep 3
done; echo " ready."
printf "→ Waiting for MCP server "
until curl -s -m 3 http://mcp-postgres.traefik.test/health 2>/dev/null | grep -q ok; do
  printf "."; sleep 2
done; echo " ready."

# 6. Show the logins (generated, printed once — not stored in the repo).
cat <<EOF

  ✅  Up.  MCP server:  http://mcp-postgres.traefik.test/mcp

  Demo logins (generated locally this run):
    alice  / ${ALICE_PASSWORD}   → db_admin     (sees everything)
    bob    / ${BOB_PASSWORD}   → db_analyst   (partial PII masking)
    carol  / ${CAROL_PASSWORD}   → db_readonly  (PII fully redacted)

  Add to Claude Desktop / Code as an MCP server, or:
    ./scripts/get_token.sh alice
EOF
