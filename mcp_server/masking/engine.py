"""
Column-level masking engine.

Masking is applied AFTER the database returns results.
Policy: column_name → role_tier → mask function (None = no masking).

Role tiers (highest wins):
  db_admin    → sees everything unmasked
  db_analyst  → partial masks on PII; salary / national_id redacted
  db_readonly → all sensitive columns fully masked / redacted
"""

from typing import Any, Callable, Optional

# ── Type alias ────────────────────────────────────────────────────────────────
MaskFn = Optional[Callable[[Any], Any]]

# ── Masking policies ──────────────────────────────────────────────────────────
# Structure:  column_name → { role_tier → mask_fn }
# mask_fn = None  →  no masking (pass through)
# mask_fn = callable  →  apply to raw value

def _partial_ssn(v: Any) -> str:
    s = str(v) if v is not None else ""
    return f"***-**-{s[-4:]}" if len(s) >= 4 else "***-**-****"

def _redacted_ssn(_: Any) -> str:
    return "***-**-****"

def _partial_email(v: Any) -> str:
    s = str(v) if v is not None else ""
    if "@" not in s:
        return "****@*****.***"
    local, domain = s.split("@", 1)
    return f"{local[0]}***@{domain}"

def _redacted_email(_: Any) -> str:
    return "****@*****.***"

def _partial_phone(v: Any) -> str:
    digits = "".join(c for c in str(v or "") if c.isdigit())
    if len(digits) >= 4:
        return f"***-***-{digits[-4:]}"
    return "***-***-****"

def _partial_account(v: Any) -> str:
    s = str(v) if v is not None else ""
    return f"{'*' * max(0, len(s) - 4)}{s[-4:]}" if len(s) >= 4 else "****"

def _year_only(v: Any) -> str:
    s = str(v) if v is not None else ""
    return f"{s[:4]}-**-**" if len(s) >= 4 else "****-**-**"


MASKING_POLICY: dict[str, dict[str, MaskFn]] = {
    # customers
    "ssn": {
        "db_admin":    None,
        "db_analyst":  _partial_ssn,
        "db_readonly": _redacted_ssn,
    },
    "email": {
        "db_admin":    None,
        "db_analyst":  _partial_email,
        "db_readonly": _redacted_email,
    },
    "phone": {
        "db_admin":    None,
        "db_analyst":  _partial_phone,
        "db_readonly": lambda _: "***-***-****",
    },
    "date_of_birth": {
        "db_admin":    None,
        "db_analyst":  _year_only,
        "db_readonly": lambda _: "****-**-**",
    },
    # accounts
    "account_number": {
        "db_admin":    None,
        "db_analyst":  _partial_account,
        "db_readonly": lambda _: "****************",
    },
    "balance": {
        "db_admin":    None,
        "db_analyst":  None,                       # analysts see full balance
        "db_readonly": lambda _: "[REDACTED]",
    },
    # transactions
    "merchant_raw": {
        "db_admin":    None,
        "db_analyst":  lambda v: (str(v)[:40] + "…") if v and len(str(v)) > 40 else v,
        "db_readonly": lambda _: "[REDACTED]",
    },
    "card_last4": {
        "db_admin":    None,
        "db_analyst":  None,
        "db_readonly": lambda _: "****",
    },
    # employees  (requires db_analyst+; guardrails block db_readonly before masking)
    "salary": {
        "db_admin":    None,
        "db_analyst":  lambda _: "[REDACTED]",
        "db_readonly": lambda _: "[REDACTED]",
    },
    "national_id": {
        "db_admin":    None,
        "db_analyst":  lambda v: f"***{str(v)[-4:]}" if v and len(str(v)) >= 4 else "****",
        "db_readonly": lambda _: "[REDACTED]",
    },
    "personal_email": {
        "db_admin":    None,
        "db_analyst":  _partial_email,
        "db_readonly": lambda _: "[REDACTED]",
    },
}

# Columns that were masked — reported in the audit log
SENSITIVE_COLUMNS: frozenset[str] = frozenset(MASKING_POLICY.keys())


# ── Public API ────────────────────────────────────────────────────────────────

def mask_rows(
    columns: list[str],
    rows: list[tuple],
    role_tier: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Apply masking policies to query results.

    Returns:
        (masked_records, sensitive_columns_accessed)

    masked_records:            list of dicts  {column: value}
    sensitive_columns_accessed: column names that had a mask applied
    """
    if role_tier not in ("db_admin", "db_analyst", "db_readonly"):
        role_tier = "db_readonly"

    # Which columns in this result set are sensitive?
    active_sensitive = [c for c in columns if c in MASKING_POLICY]

    # Build mask functions for this role once (avoids repeated dict lookups)
    mask_fns: dict[str, MaskFn] = {
        col: MASKING_POLICY[col].get(role_tier)
        for col in active_sensitive
    }

    masked_records: list[dict[str, Any]] = []
    for row in rows:
        record: dict[str, Any] = {}
        for col, val in zip(columns, row):
            fn = mask_fns.get(col)
            if fn is not None:
                # Apply mask only to non-None values
                record[col] = fn(val) if val is not None else None
            else:
                record[col] = val
        masked_records.append(record)

    # Report which sensitive columns were present (regardless of whether masked)
    accessed_sensitive = [
        col for col in active_sensitive
        if mask_fns.get(col) is not None  # actually masked for this role
    ]

    return masked_records, accessed_sensitive
