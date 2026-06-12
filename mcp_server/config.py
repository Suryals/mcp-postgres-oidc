from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    database_url: str = "postgresql://mcp_user:mcp_secret@localhost:5432/banking_db"

    # Keycloak realm URL — the same value works from the server and the browser
    # (Traefik carries a keycloak.test alias on the internal network).
    keycloak_public_url: str = "http://keycloak.test"
    keycloak_realm: str = "mcp-db"

    # Public base URL of THIS server (advertised in OAuth resource metadata)
    public_base_url: str = "http://mcp-postgres.traefik.test"

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
        """Issuer string Keycloak stamps into tokens (logged at startup)."""
        return f"{self.keycloak_public_url}/realms/{self.keycloak_realm}"


settings = Settings()
