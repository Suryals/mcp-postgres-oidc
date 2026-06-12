#!/usr/bin/env bash
# Configure Keycloak Authorization Services on the postgres-resource client so
# kc_validator's UMA decision (permission = appdb#<role>) resolves against the
# user's realm role. Idempotent-ish: run once after Keycloak is up.
set -euo pipefail
KC=${KC:-https://localhost:8443}
C() { curl -sk "$@"; }

ADM=$(C -X POST "$KC/realms/master/protocol/openid-connect/token" \
  -d 'grant_type=password&client_id=admin-cli&username=admin&password=admin' | jq -r .access_token)
H=(-H "Authorization: Bearer $ADM" -H 'Content-Type: application/json')
RS=$(C "${H[@]}" "$KC/admin/realms/pgoauth/clients?clientId=postgres-resource" | jq -r '.[0].id')
echo "resource-server client id: $RS"
BASE="$KC/admin/realms/pgoauth/clients/$RS/authz/resource-server"

# 1. authz scopes = the three DB roles
for s in db_admin db_analyst db_readonly; do
  C "${H[@]}" -X POST "$BASE/scope" -d "{\"name\":\"$s\"}" >/dev/null || true
done
scope_id() { C "${H[@]}" "$BASE/scope?name=$1" | jq -r '.[0].id'; }

# 2. resource "appdb" carrying those scopes
SC_ADMIN=$(scope_id db_admin); SC_ANALYST=$(scope_id db_analyst); SC_RO=$(scope_id db_readonly)
C "${H[@]}" -X POST "$BASE/resource" -d "{\"name\":\"appdb\",\"displayName\":\"appdb\"}" >/dev/null || true
APPDB=$(C "${H[@]}" "$BASE/resource?name=appdb" | jq -r '.[0]._id')
# Attach all three scopes explicitly (PUT — avoids a create-time race).
C "${H[@]}" -X PUT "$BASE/resource/$APPDB" -d "{
  \"name\":\"appdb\",\"_id\":\"$APPDB\",
  \"scopes\":[{\"name\":\"db_admin\"},{\"name\":\"db_analyst\"},{\"name\":\"db_readonly\"}]
}" >/dev/null

# 3. role policies (user must hold the realm role)
role_id() { C "${H[@]}" "$KC/admin/realms/pgoauth/roles/$1" | jq -r '.id'; }
mk_role_policy() {
  local role=$1 rid; rid=$(role_id "$role")
  C "${H[@]}" -X POST "$BASE/policy/role" -d "{
    \"name\":\"has-$role\",\"logic\":\"POSITIVE\",
    \"roles\":[{\"id\":\"$rid\",\"required\":true}]
  }" >/dev/null || true
  C "${H[@]}" "$BASE/policy?name=has-$role" | jq -r '.[0].id'
}
P_ADMIN=$(mk_role_policy db_admin); P_ANALYST=$(mk_role_policy db_analyst); P_RO=$(mk_role_policy db_readonly)

# 4. scope permissions  appdb#<scope>  ->  matching role policy
mk_perm() {
  local scope=$1 sid=$2 pid=$3
  C "${H[@]}" -X POST "$BASE/permission/scope" -d "{
    \"name\":\"appdb#$scope\",\"resources\":[\"$APPDB\"],\"scopes\":[\"$sid\"],
    \"policies\":[\"$pid\"],\"decisionStrategy\":\"AFFIRMATIVE\"
  }" >/dev/null && echo "  permission appdb#$scope -> has-$scope"
}
mk_perm db_admin    "$SC_ADMIN"   "$P_ADMIN"
mk_perm db_analyst  "$SC_ANALYST" "$P_ANALYST"
mk_perm db_readonly "$SC_RO"      "$P_RO"
echo "authz configured."
