#!/usr/bin/env bash
# Generate the throwaway self-signed CA + Keycloak TLS cert for the spike.
# Run once before `docker compose up`. Nothing here is a real secret.
set -euo pipefail
cd "$(dirname "$0")/certs" 2>/dev/null || { mkdir -p "$(dirname "$0")/certs"; cd "$(dirname "$0")/certs"; }

openssl req -x509 -newkey rsa:2048 -nodes -keyout ca.key -out ca.crt -days 3650 \
  -subj "/CN=pg18-oauth-spike-CA" 2>/dev/null

openssl req -newkey rsa:2048 -nodes -keyout kc.key -out kc.csr \
  -subj "/CN=keycloak.pg.test" 2>/dev/null
printf 'subjectAltName=DNS:keycloak.pg.test,DNS:keycloak,DNS:localhost\n' > kc.ext
openssl x509 -req -in kc.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
  -out kc.crt -days 3650 -extfile kc.ext 2>/dev/null
rm -f kc.csr kc.ext ca.srl
echo "certs generated in $(pwd):"; ls -1
