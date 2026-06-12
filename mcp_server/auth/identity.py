"""
Caller identity, derived from the token FastMCP has already verified.

FastMCP's Keycloak provider validates the bearer token (signature, issuer, expiry)
before any tool runs, and exposes the decoded claims via get_access_token().
We map those claims to a UserContext that guardrails + masking consume.
"""

from dataclasses import dataclass, field

from fastmcp.server.dependencies import get_access_token


@dataclass
class UserContext:
    user_id: str
    email: str
    username: str
    roles: list[str]              # DB roles: db_admin | db_analyst | db_readonly
    scopes: list[str]
    raw_claims: dict = field(default_factory=dict, repr=False)

    @property
    def role_tier(self) -> str:
        """Highest-privilege DB role this user holds (defaults to readonly)."""
        if "db_admin" in self.roles:
            return "db_admin"
        if "db_analyst" in self.roles:
            return "db_analyst"
        return "db_readonly"

    @property
    def can_access_sensitive(self) -> bool:
        return self.role_tier in ("db_admin", "db_analyst")

    @property
    def can_access_admin(self) -> bool:
        return self.role_tier == "db_admin"


def get_current_user() -> UserContext:
    """Build a UserContext from the verified token's claims (Keycloak realm roles)."""
    token = get_access_token()
    if token is None:
        raise PermissionError("No authenticated user in context")

    claims = token.claims or {}
    realm_roles = (claims.get("realm_access") or {}).get("roles", [])
    db_roles = [r for r in realm_roles if r.startswith("db_")]

    return UserContext(
        user_id=claims.get("sub", ""),
        email=claims.get("email", ""),
        username=claims.get("preferred_username", claims.get("sub", "")),
        roles=db_roles,
        scopes=(claims.get("scope") or "").split(),
        raw_claims=claims,
    )
