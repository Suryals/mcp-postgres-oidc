# PostgreSQL 18 with the CloudNativePG Keycloak OAuth validator.
#
# The validator (kc_validator.so) is copied straight from CloudNativePG's testing
# image (same base: Debian trixie, PG18) so the ABI matches stock postgres:18.
FROM ghcr.io/cloudnative-pg/postgres-keycloak-oauth-validator-testing:18-dev-trixie AS validator

FROM postgres:18
# The validator calls Keycloak's UMA decision endpoint over libcurl (HTTPS),
# so it needs libcurl + trust for the spike CA.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libcurl4 ca-certificates \
 && rm -rf /var/lib/apt/lists/*
COPY --from=validator /lib/kc_validator.so /usr/lib/postgresql/18/lib/kc_validator.so
COPY certs/ca.crt /usr/local/share/ca-certificates/spike-ca.crt
RUN update-ca-certificates
