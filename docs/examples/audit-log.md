# Audit at the source — real `get_audit_log` output

Captured live from Claude Desktop calling `get_audit_log` as `alice` (db_admin).
Every PII access is attributed to the **real human principal** — not a service
account — with the role used and which columns were masked. This is the
"who accessed this customer's data, and what were they allowed to see?" trail that
SOC 2 / PCI / GDPR require.

| ID | User | Role | Query | Rows | Sensitive cols | Time (UTC) |
|----|------|------|-------|------|----------------|------------|
| 8 | alice@banking.demo | db_admin | `SELECT first_name, last_name, ssn, email FROM customers LIMIT 3` | 3 | — | 06-13 20:13 |
| 7 | alice@banking.demo | db_admin | `SELECT current_user, current_setting('role'…)` | 1 | — | 06-13 19:52 |
| 6 | carol@banking.demo | db_readonly | `SELECT ssn, email, phone, date_of_birth FROM customers …` | 1 | ssn, email, phone, dob | 06-13 19:44 |
| 5 | bob@banking.demo | db_analyst | `SELECT ssn, email, phone, date_of_birth FROM customers …` | 1 | ssn, email, phone, dob | 06-13 19:44 |
| 4 | alice@banking.demo | db_admin | `SELECT ssn, email, phone, date_of_birth FROM customers …` | 1 | — | 06-13 19:44 |
| 3 | carol@banking.demo | db_readonly | `SELECT first_name, ssn, email, date_of_birth …` | 3 | ssn, email, dob | 06-13 19:40 |
| 2 | bob@banking.demo | db_analyst | `SELECT first_name, ssn, email, date_of_birth …` | 3 | ssn, email, dob | 06-13 19:40 |
| 1 | alice@banking.demo | db_admin | `SELECT first_name, ssn, email, date_of_birth …` | 3 | — | 06-13 19:40 |

## What this demonstrates

- **AuthN** — each row names the authenticated user (`alice`/`bob`/`carol`), resolved
  from the OIDC token, independent of the database connection.
- **AuthZ** — the `Role` and `Sensitive cols` columns record the privilege used and
  exactly what was masked: empty for `db_admin` (unmasked), populated for
  `db_analyst`/`db_readonly`.
- **Audit** — an immutable, per-principal trail of every query against PII.

> Note on the two identities (Tier 1): row #7's `SELECT current_user` returns the
> shared Postgres connection account, yet the audit row is still attributed to
> `alice` — because the app logs the **OIDC principal**, not the DB connection. In
> the Tier-2 design ([`experiments/pg18-oauth/`](../../experiments/pg18-oauth/)),
> `current_user` *is* the user's role and the audit lives at the database itself.
