# PostgreSQL 18 with the CloudNativePG Keycloak OAuth validator.
#
# The validator (kc_validator.so) is copied straight from CloudNativePG's testing
# image (same base: Debian trixie, PG18) so the ABI matches stock postgres:18 —
# no vendored binary, fully reproducible.
FROM ghcr.io/cloudnative-pg/postgres-keycloak-oauth-validator-testing:18-dev-trixie AS validator

FROM postgres:18
# The validator calls Keycloak's UMA decision endpoint over libcurl.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libcurl4 \
 && rm -rf /var/lib/apt/lists/*
COPY --from=validator /lib/kc_validator.so /usr/lib/postgresql/18/lib/kc_validator.so
