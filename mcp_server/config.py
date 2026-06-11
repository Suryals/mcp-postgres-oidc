from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    database_url: str = "postgresql://mcp_user:mcp_secret@localhost:5432/banking_db"

    # Keycloak — two URLs to handle split-horizon DNS
    # Internal: used by the container to fetch JWKS (direct container-to-container)
    keycloak_internal_url: str = "http://auth-keycloak:8080"
    # External issuer: must match the 'iss' claim in tokens (what Keycloak stamps)
    keycloak_issuer: str = "http://keycloak.test/realms/mcp-db"
    keycloak_realm: str = "mcp-db"

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
    def jwks_uri(self) -> str:
        return (
            f"{self.keycloak_internal_url}/realms/{self.keycloak_realm}"
            "/protocol/openid-connect/certs"
        )


settings = Settings()
