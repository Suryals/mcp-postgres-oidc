-- ============================================================
-- MCP Postgres OIDC Demo — Banking Schema
-- ============================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ── customers ───────────────────────────────────────────────
-- PII: ssn, dob, email, phone  (masking tiers applied in MCP)
CREATE TABLE customers (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    first_name      VARCHAR(100)  NOT NULL,
    last_name       VARCHAR(100)  NOT NULL,
    email           VARCHAR(255)  NOT NULL UNIQUE,
    phone           VARCHAR(20),
    date_of_birth   DATE,
    ssn             VARCHAR(11),            -- XXX-XX-XXXX
    address_line1   VARCHAR(255),
    address_city    VARCHAR(100),
    address_state   CHAR(2),
    address_zip     VARCHAR(10),
    kyc_status      VARCHAR(20)   NOT NULL DEFAULT 'pending'
                        CHECK (kyc_status IN ('pending','verified','rejected')),
    created_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

-- ── accounts ────────────────────────────────────────────────
-- Sensitive: account_number, balance
CREATE TABLE accounts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id     UUID          NOT NULL REFERENCES customers(id),
    account_number  VARCHAR(20)   NOT NULL UNIQUE,
    account_type    VARCHAR(20)   NOT NULL
                        CHECK (account_type IN ('checking','savings','credit','investment')),
    balance         NUMERIC(15,2) NOT NULL DEFAULT 0,
    currency        CHAR(3)       NOT NULL DEFAULT 'USD',
    status          VARCHAR(20)   NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active','frozen','closed')),
    opened_at       TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

-- ── transactions ─────────────────────────────────────────────
-- Sensitive: merchant_raw, card_last4
CREATE TABLE transactions (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id       UUID          NOT NULL REFERENCES accounts(id),
    amount           NUMERIC(15,2) NOT NULL,
    transaction_type VARCHAR(20)   NOT NULL
                         CHECK (transaction_type IN ('credit','debit','transfer','fee','refund')),
    merchant_name    VARCHAR(255),
    merchant_raw     VARCHAR(500),          -- raw descriptor from payment network
    card_last4       CHAR(4),
    status           VARCHAR(20)   NOT NULL DEFAULT 'completed'
                         CHECK (status IN ('pending','completed','failed','reversed')),
    reference_id     VARCHAR(64)   UNIQUE,
    created_at       TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    description      TEXT
);

-- ── employees ────────────────────────────────────────────────
-- Sensitive: salary, national_id, personal_email  (scope: db:sensitive)
CREATE TABLE employees (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    first_name      VARCHAR(100)  NOT NULL,
    last_name       VARCHAR(100)  NOT NULL,
    work_email      VARCHAR(255)  NOT NULL UNIQUE,
    personal_email  VARCHAR(255),
    department      VARCHAR(100),
    title           VARCHAR(150),
    salary          NUMERIC(12,2),
    national_id     VARCHAR(20),
    hire_date       DATE,
    manager_id      UUID REFERENCES employees(id),
    is_active       BOOLEAN       NOT NULL DEFAULT TRUE
);

-- ── audit_logs ───────────────────────────────────────────────
-- Append-only. Only db_admin role can query this.
CREATE TABLE audit_logs (
    id                BIGSERIAL    PRIMARY KEY,
    user_id           VARCHAR(255) NOT NULL,
    user_email        VARCHAR(255),
    user_roles        TEXT[]       NOT NULL DEFAULT '{}',
    tool_name         VARCHAR(100),
    query_preview     VARCHAR(500),
    query_hash        CHAR(64),              -- SHA-256 of raw query
    rows_returned     INTEGER,
    sensitive_columns TEXT[]       DEFAULT '{}',
    execution_ms      INTEGER,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ── indexes ──────────────────────────────────────────────────
CREATE INDEX idx_accounts_customer_id   ON accounts(customer_id);
CREATE INDEX idx_transactions_account   ON transactions(account_id);
CREATE INDEX idx_transactions_created   ON transactions(created_at DESC);
CREATE INDEX idx_employees_department   ON employees(department);
CREATE INDEX idx_audit_logs_user_id     ON audit_logs(user_id);
CREATE INDEX idx_audit_logs_created     ON audit_logs(created_at DESC);
CREATE INDEX idx_customers_kyc          ON customers(kyc_status);
