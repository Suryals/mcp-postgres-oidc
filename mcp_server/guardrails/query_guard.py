"""
Query guardrail layer.

Enforces:
  • SELECT-only (no DDL, DML, TCL)
  • No dangerous keywords (DROP, TRUNCATE, …)
  • Table-level access control (sensitive/admin tables require roles)
  • Automatic LIMIT injection / enforcement
  • Basic rate limiting (in-memory, per user_id)

All checks raise GuardrailError — callers turn this into a user-visible error.
"""

import hashlib
import re
import time
from collections import defaultdict, deque

import sqlparse
from sqlparse.sql import Statement

from config import settings

# ── Exceptions ───────────────────────────────────────────────────────────────

class GuardrailError(Exception):
    """Raised when a query violates a guardrail policy."""


# ── Table access policy ──────────────────────────────────────────────────────
# Maps table name → minimum role required to query it.
TABLE_ACCESS = {
    "employees":  "db_analyst",   # requires db:sensitive scope (analyst+)
    "audit_logs": "db_admin",     # admin only
}

ROLE_ORDER = {"db_admin": 3, "db_analyst": 2, "db_readonly": 1}

FORBIDDEN_KEYWORDS = frozenset({
    "DROP", "DELETE", "UPDATE", "INSERT", "CREATE", "ALTER",
    "TRUNCATE", "GRANT", "REVOKE", "EXECUTE", "CALL", "COPY",
    "VACUUM", "ANALYZE", "REINDEX",
})

# ── Rate limiter (token bucket / sliding window) ─────────────────────────────
# Stores per-user timestamps of recent requests.
_rate_windows: dict[str, deque] = defaultdict(deque)


def _check_rate_limit(user_id: str) -> None:
    now = time.monotonic()
    window = 60.0  # 1-minute sliding window
    dq = _rate_windows[user_id]

    # Evict expired entries
    while dq and now - dq[0] > window:
        dq.popleft()

    if len(dq) >= settings.rate_limit_rpm:
        raise GuardrailError(
            f"Rate limit exceeded: max {settings.rate_limit_rpm} queries/min per user"
        )

    dq.append(now)


# ── SQL helpers ───────────────────────────────────────────────────────────────

def _get_statement_type(query: str) -> str | None:
    """Return the statement type (SELECT, INSERT, …) or None."""
    parsed = sqlparse.parse(query.strip())
    if not parsed:
        return None
    return parsed[0].get_type()


def _extract_table_names(query: str) -> set[str]:
    """
    Best-effort extraction of table names from FROM / JOIN clauses.
    Does not handle CTEs or sub-selects perfectly — good enough for guardrails.
    """
    tables: set[str] = set()
    # Match: FROM table_name  or  JOIN table_name
    for match in re.finditer(
        r'\b(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_]*)',
        query,
        re.IGNORECASE,
    ):
        tables.add(match.group(1).lower())
    return tables


def _inject_limit(query: str) -> str:
    """
    Ensure the query has a LIMIT ≤ MAX_ROWS.
    Replaces existing LIMIT if it exceeds the cap.
    Appends one if absent.
    """
    cap = settings.max_rows
    # Check existing LIMIT
    limit_match = re.search(r'\bLIMIT\s+(\d+)', query, re.IGNORECASE)
    if limit_match:
        existing = int(limit_match.group(1))
        if existing > cap:
            query = re.sub(
                r'\bLIMIT\s+\d+', f'LIMIT {cap}', query, flags=re.IGNORECASE
            )
    else:
        query = query.rstrip("; \n") + f" LIMIT {cap}"

    return query


# ── Main entry point ──────────────────────────────────────────────────────────

def validate_and_rewrite(query: str, roles: list[str], user_id: str) -> tuple[str, str]:
    """
    Validate the query against all guardrails and return (clean_query, query_hash).

    Raises GuardrailError if any check fails.
    """
    # 1. Rate limit
    _check_rate_limit(user_id)

    # 2. Non-empty
    query = query.strip()
    if not query:
        raise GuardrailError("Query cannot be empty")

    # 3. Single statement only
    statements = [s for s in sqlparse.parse(query) if s.value.strip()]
    if len(statements) > 1:
        raise GuardrailError("Only a single SQL statement is allowed per call")

    # 4. Must be SELECT
    stmt_type = _get_statement_type(query)
    if stmt_type != "SELECT":
        raise GuardrailError(
            f"Only SELECT statements are allowed. Received: {stmt_type or 'unknown'}"
        )

    # 5. Forbidden keyword scan (belt-and-suspenders over the type check)
    upper = query.upper()
    for kw in FORBIDDEN_KEYWORDS:
        if re.search(rf'\b{re.escape(kw)}\b', upper):
            raise GuardrailError(f"Statement contains forbidden keyword: {kw}")

    # 6. Table-level access control
    tables = _extract_table_names(query)
    user_level = max((ROLE_ORDER.get(r, 0) for r in roles), default=0)

    for table in tables:
        required_role = TABLE_ACCESS.get(table)
        if required_role:
            required_level = ROLE_ORDER.get(required_role, 0)
            if user_level < required_level:
                raise GuardrailError(
                    f"Access denied: table '{table}' requires role '{required_role}' "
                    f"(your highest role: {_role_name(user_level)})"
                )

    # 7. Inject / enforce LIMIT
    query = _inject_limit(query)

    # 8. Compute hash for audit log
    query_hash = hashlib.sha256(query.encode()).hexdigest()

    return query, query_hash


def _role_name(level: int) -> str:
    return {3: "db_admin", 2: "db_analyst", 1: "db_readonly"}.get(level, "none")
