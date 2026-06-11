"""
MCP tool definitions — the tools Claude (or any MCP client) can call.

Every tool:
  1. Reads the current UserContext from the auth ContextVar
  2. Passes the query through guardrails
  3. Executes against PostgreSQL
  4. Applies column masking
  5. Writes an audit log entry
  6. Returns a structured result
"""

import asyncio
import hashlib
import json
import logging
import time
from typing import Any

import asyncpg
from fastmcp import FastMCP

from auth.oidc import current_user, UserContext
from guardrails.query_guard import validate_and_rewrite, GuardrailError
from masking.engine import mask_rows
from config import settings

logger = logging.getLogger(__name__)

# Injected by server.py after pool creation
_pool: asyncpg.Pool | None = None


def register_tools(mcp: FastMCP, pool: asyncpg.Pool) -> None:
    """Bind the connection pool and register all tools on the FastMCP instance."""
    global _pool
    _pool = pool
    _register(mcp)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_user() -> UserContext:
    user = current_user.get()
    if user is None:
        raise PermissionError("No authenticated user in context")
    return user


async def _write_audit(
    conn: asyncpg.Connection,
    user: UserContext,
    tool_name: str,
    query: str,
    query_hash: str,
    rows_returned: int,
    sensitive_columns: list[str],
    execution_ms: int,
) -> None:
    await conn.execute(
        """
        INSERT INTO audit_logs
            (user_id, user_email, user_roles, tool_name, query_preview,
             query_hash, rows_returned, sensitive_columns, execution_ms)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        """,
        user.user_id,
        user.email,
        user.roles,
        tool_name,
        query[:500],
        query_hash,
        rows_returned,
        sensitive_columns,
        execution_ms,
    )


def _fmt(records: list[dict], row_count: int, query_hash: str) -> str:
    return json.dumps(
        {
            "row_count": row_count,
            "query_hash": query_hash[:12],
            "data": records,
        },
        default=str,
        indent=2,
    )


# ── Tool registration ─────────────────────────────────────────────────────────

def _register(mcp: FastMCP) -> None:

    @mcp.tool()
    async def list_tables() -> str:
        """
        List all tables in the database with their sensitivity classification.
        Available to all authenticated users.
        """
        user = _get_user()
        async with _pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    table_name,
                    pg_size_pretty(pg_total_relation_size(quote_ident(table_name))) AS size,
                    (SELECT COUNT(*) FROM information_schema.columns c
                     WHERE c.table_name = t.table_name
                       AND c.table_schema = 'public') AS column_count
                FROM information_schema.tables t
                WHERE table_schema = 'public'
                  AND table_type = 'BASE TABLE'
                ORDER BY table_name
                """
            )

        sensitivity = {
            "customers":    "PII — ssn, email, phone, dob masked by role",
            "accounts":     "FINANCIAL — account_number, balance masked by role",
            "transactions": "FINANCIAL — merchant_raw, card_last4 masked by role",
            "employees":    "SENSITIVE — salary, national_id (requires db_analyst+)",
            "audit_logs":   "ADMIN — full query audit trail (requires db_admin)",
        }

        tables = []
        for r in rows:
            name = r["table_name"]
            # Filter tables the user can't see at all
            if name == "audit_logs" and not user.can_access_admin:
                continue
            if name == "employees" and not user.can_access_sensitive:
                continue
            tables.append({
                "table": name,
                "rows_approx": r["size"],
                "columns": r["column_count"],
                "sensitivity": sensitivity.get(name, "—"),
            })

        return json.dumps({"your_role": user.role_tier, "tables": tables}, indent=2)


    @mcp.tool()
    async def describe_table(table_name: str) -> str:
        """
        Return the schema for a table including column types and masking policy
        for the current user's role.

        Args:
            table_name: Name of the table to describe.
        """
        user = _get_user()
        table_name = table_name.lower().strip()

        # Access check
        if table_name == "audit_logs" and not user.can_access_admin:
            return json.dumps({"error": "audit_logs requires db_admin role"})
        if table_name == "employees" and not user.can_access_sensitive:
            return json.dumps({"error": "employees table requires db_analyst+ role"})

        async with _pool.acquire() as conn:
            columns = await conn.fetch(
                """
                SELECT column_name, data_type, is_nullable, column_default
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name   = $1
                ORDER BY ordinal_position
                """,
                table_name,
            )

        if not columns:
            return json.dumps({"error": f"Table '{table_name}' not found"})

        from masking.engine import MASKING_POLICY
        schema = []
        for col in columns:
            name = col["column_name"]
            policy = MASKING_POLICY.get(name, {})
            mask_fn = policy.get(user.role_tier)
            schema.append({
                "column":    name,
                "type":      col["data_type"],
                "nullable":  col["is_nullable"] == "YES",
                "masked":    mask_fn is not None,
                "mask_note": f"masked for {user.role_tier}" if mask_fn else "visible",
            })

        return json.dumps({
            "table":    table_name,
            "your_role": user.role_tier,
            "columns":  schema,
        }, indent=2)


    @mcp.tool()
    async def query(sql: str) -> str:
        """
        Execute a read-only SQL SELECT query against the banking database.

        Guardrails enforced automatically:
          - SELECT-only (no DML/DDL)
          - Max 1000 rows returned
          - Query timeout: 10 seconds
          - Sensitive tables require appropriate roles
          - All results masked per your role tier

        Args:
            sql: A SQL SELECT statement.
        """
        user = _get_user()
        t0 = time.monotonic()

        try:
            clean_sql, query_hash = validate_and_rewrite(sql, user.roles, user.user_id)
        except GuardrailError as e:
            return json.dumps({"error": "Guardrail violation", "detail": str(e)})

        try:
            async with _pool.acquire() as conn:
                async with conn.transaction(readonly=True):
                    rows = await asyncio.wait_for(
                        conn.fetch(clean_sql),
                        timeout=settings.query_timeout_seconds,
                    )
        except asyncio.TimeoutError:
            return json.dumps({"error": f"Query timed out after {settings.query_timeout_seconds}s"})
        except asyncpg.PostgresError as e:
            logger.warning("DB error for user %s: %s", user.username, e)
            return json.dumps({"error": "Database error", "detail": str(e)})

        execution_ms = int((time.monotonic() - t0) * 1000)

        if not rows:
            return json.dumps({"row_count": 0, "data": [], "query_hash": query_hash[:12]})

        columns = list(rows[0].keys())
        raw_rows = [tuple(r) for r in rows]
        masked_records, sensitive_accessed = mask_rows(columns, raw_rows, user.role_tier)

        # Audit (fire-and-forget — don't block the response)
        async with _pool.acquire() as conn:
            await _write_audit(
                conn, user, "query", clean_sql, query_hash,
                len(masked_records), sensitive_accessed, execution_ms,
            )

        return _fmt(masked_records, len(masked_records), query_hash)


    @mcp.tool()
    async def search_customers(
        name: str = "",
        email: str = "",
        kyc_status: str = "",
    ) -> str:
        """
        Search customers by name, email, or KYC status.
        Results are masked according to your role tier.

        Args:
            name:       Partial first or last name (case-insensitive).
            email:      Partial email address.
            kyc_status: Filter by KYC status: pending | verified | rejected
        """
        user = _get_user()

        conditions = ["TRUE"]
        params: list[Any] = []

        if name:
            params.append(f"%{name}%")
            conditions.append(
                f"(first_name ILIKE ${len(params)} OR last_name ILIKE ${len(params)})"
            )
        if email:
            params.append(f"%{email}%")
            conditions.append(f"email ILIKE ${len(params)}")
        if kyc_status and kyc_status in ("pending", "verified", "rejected"):
            params.append(kyc_status)
            conditions.append(f"kyc_status = ${len(params)}")

        sql = f"""
            SELECT id, first_name, last_name, email, phone,
                   date_of_birth, ssn, address_city, address_state, kyc_status
            FROM customers
            WHERE {' AND '.join(conditions)}
            ORDER BY last_name, first_name
            LIMIT {settings.max_rows}
        """

        t0 = time.monotonic()
        async with _pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)

        execution_ms = int((time.monotonic() - t0) * 1000)
        columns = ["id", "first_name", "last_name", "email", "phone",
                   "date_of_birth", "ssn", "address_city", "address_state", "kyc_status"]
        raw_rows = [tuple(r) for r in rows]
        masked_records, sensitive_accessed = mask_rows(columns, raw_rows, user.role_tier)

        query_hash = hashlib.sha256(sql.encode()).hexdigest()
        async with _pool.acquire() as conn:
            await _write_audit(
                conn, user, "search_customers", sql, query_hash,
                len(masked_records), sensitive_accessed, execution_ms,
            )

        return _fmt(masked_records, len(masked_records), query_hash)


    @mcp.tool()
    async def get_transaction_summary(
        account_id: str = "",
        limit: int = 20,
    ) -> str:
        """
        Return recent transactions for an account (or all accounts up to limit).
        Merchant raw descriptors and card data are masked per role.

        Args:
            account_id: UUID of the account. Leave empty to see recent across all accounts.
            limit:      Number of transactions to return (max 100).
        """
        user = _get_user()
        limit = min(limit, 100)

        if account_id:
            sql = """
                SELECT t.id, t.account_id, t.amount, t.transaction_type,
                       t.merchant_name, t.merchant_raw, t.card_last4,
                       t.status, t.reference_id, t.created_at
                FROM transactions t
                WHERE t.account_id = $1
                ORDER BY t.created_at DESC
                LIMIT $2
            """
            params = [account_id, limit]
        else:
            sql = """
                SELECT t.id, t.account_id, t.amount, t.transaction_type,
                       t.merchant_name, t.merchant_raw, t.card_last4,
                       t.status, t.reference_id, t.created_at
                FROM transactions t
                ORDER BY t.created_at DESC
                LIMIT $1
            """
            params = [limit]

        t0 = time.monotonic()
        async with _pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)

        execution_ms = int((time.monotonic() - t0) * 1000)
        columns = ["id", "account_id", "amount", "transaction_type",
                   "merchant_name", "merchant_raw", "card_last4",
                   "status", "reference_id", "created_at"]
        raw_rows = [tuple(r) for r in rows]
        masked_records, sensitive_accessed = mask_rows(columns, raw_rows, user.role_tier)

        query_hash = hashlib.sha256(sql.encode()).hexdigest()
        async with _pool.acquire() as conn:
            await _write_audit(
                conn, user, "get_transaction_summary", sql, query_hash,
                len(masked_records), sensitive_accessed, execution_ms,
            )

        return _fmt(masked_records, len(masked_records), query_hash)


    @mcp.tool()
    async def get_audit_log(limit: int = 50) -> str:
        """
        Return recent audit log entries. Requires db_admin role.

        Args:
            limit: Number of entries to return (max 200).
        """
        user = _get_user()
        if not user.can_access_admin:
            return json.dumps({"error": "audit_logs requires db_admin role"})

        limit = min(limit, 200)
        async with _pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, user_id, user_email, user_roles, tool_name,
                       query_preview, rows_returned, sensitive_columns,
                       execution_ms, created_at
                FROM audit_logs
                ORDER BY created_at DESC
                LIMIT $1
                """,
                limit,
            )

        records = [dict(r) for r in rows]
        return json.dumps({"row_count": len(records), "data": records}, default=str, indent=2)
