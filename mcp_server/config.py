from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    database_url: str = "postgresql://mcp_user:mcp_secret@localhost:5432/banking_db"

    # Keycloak — split-horizon URLs
    # Internal: container-to-container, used to fetch JWKS and exchange tokens
    keycloak_internal_url: str = "http://keycloak:8080"
    # Public: browser-facing base URL; also the host stamped into the token issuer
    keycloak_public_url: str = "http://keycloak.test"
    keycloak_realm: str = "mcp-db"

    # Public base URL of THIS server (used to build the OAuth callback URL)
    public_base_url: str = "http://mcp-postgres.traefik.test"

    # PKCE client (public client — no secret)
    pkce_client_id: str = "mcp-cli"

    # Guardrail settings
    max_rows: int = 1000
    query_timeout_seconds: int = 10
    rate_limit_rpm: int = 60

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"

    @property
    def keycloak_issuer(self) -> str:
        """Issuer string Keycloak stamps into tokens — must match for validation."""
        return f"{self.keycloak_public_url}/realms/{self.keycloak_realm}"

    @property
    def jwks_uri(self) -> str:
        return (
            f"{self.keycloak_internal_url}/realms/{self.keycloak_realm}"
            "/protocol/openid-connect/certs"
        )


settings = Settings()
