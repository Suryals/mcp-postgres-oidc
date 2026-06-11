#!/usr/bin/env python3
"""
Seed the banking_db with realistic demo data using Faker.

Usage:
    uv run --with faker --with asyncpg scripts/seed.py
    # or with custom DB:
    DATABASE_URL=postgresql://user:pass@host/db uv run --with faker --with asyncpg scripts/seed.py
"""
import asyncio
import os
import random
import hashlib
import string
from datetime import date, timedelta
from decimal import Decimal

import asyncpg
from faker import Faker

fake = Faker("en_US")
random.seed(42)
Faker.seed(42)

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://mcp_user:mcp_secret@localhost:5432/banking_db"
)

# ── volume ───────────────────────────────────────────────────
NUM_CUSTOMERS  = 2_000
NUM_EMPLOYEES  = 200
ACCTS_PER_CUST = (1, 3)   # min, max accounts per customer
TXN_PER_ACCT   = (5, 40)  # min, max transactions per account


def random_ssn() -> str:
    area = random.randint(100, 899)
    group = random.randint(10, 99)
    serial = random.randint(1000, 9999)
    return f"{area:03d}-{group:02d}-{serial:04d}"


def random_account_number() -> str:
    return "".join(random.choices(string.digits, k=16))


def random_card_last4() -> str:
    return "".join(random.choices(string.digits, k=4))


MERCHANTS = [
    ("Amazon", "AMAZON.COM*AB1CD2EF3 SEATTLE WA"),
    ("Starbucks", "STARBUCKS #12345 SAN JOSE CA"),
    ("Netflix", "NETFLIX.COM 408-555-0100 CA"),
    ("Uber", "UBER *TRIP HELP.UBER.COM CA"),
    ("Walmart", "WAL-MART #3456 DALLAS TX"),
    ("Apple", "APPLE.COM/BILL 866-712-7753 CA"),
    ("Target", "TARGET 00023456 AUSTIN TX"),
    ("Venmo", "VENMO PAYMENT 855-812-4430 NY"),
    ("PayPal", "PAYPAL *TRANSFER 402-935-7733 NE"),
    ("Whole Foods", "WHOLE FOODS MKT #10234 SF CA"),
    ("Lyft", "LYFT *RIDE SUN 6AM 855-865-9553 CA"),
    ("Spotify", "SPOTIFY P08Z6ABCDE 8888-SPOTIFY SE"),
    ("DoorDash", "DOORDASH*CHIPOTLE 855-973-1040 CA"),
    ("Chevron", "CHEVRON #9876 LOS ANGELES CA"),
]

DEPARTMENTS = [
    "Engineering", "Data Science", "Operations", "Risk & Compliance",
    "Finance", "Legal", "Product", "Customer Success", "Security",
]

TITLES = {
    "Engineering": ["Software Engineer", "Senior Engineer", "Staff Engineer", "Engineering Manager"],
    "Data Science": ["Data Analyst", "Senior Data Scientist", "ML Engineer"],
    "Operations": ["Operations Analyst", "Senior Operations Manager", "Director of Operations"],
    "Risk & Compliance": ["Compliance Analyst", "Risk Manager", "AML Specialist"],
    "Finance": ["Financial Analyst", "Senior Accountant", "CFO"],
    "Legal": ["Legal Counsel", "Senior Attorney", "General Counsel"],
    "Product": ["Product Manager", "Senior PM", "Director of Product"],
    "Customer Success": ["CS Representative", "CS Manager", "VP Customer Success"],
    "Security": ["Security Analyst", "Senior Security Engineer", "CISO"],
}


async def seed(conn: asyncpg.Connection) -> None:
    print("Truncating existing data …")
    await conn.execute(
        "TRUNCATE audit_logs, transactions, accounts, customers, employees RESTART IDENTITY CASCADE"
    )

    # ── employees ────────────────────────────────────────────
    print(f"Seeding {NUM_EMPLOYEES} employees …")
    manager_ids: list[str] = []
    employee_ids: list[str] = []
    emp_rows = []

    for i in range(NUM_EMPLOYEES):
        dept = random.choice(DEPARTMENTS)
        title = random.choice(TITLES[dept])
        is_manager = "Manager" in title or "Director" in title or "VP" in title or "CISO" in title or "CFO" in title or "General" in title
        base_salary = random.randint(60_000, 300_000)

        emp_rows.append((
            fake.first_name(),
            fake.last_name(),
            fake.unique.company_email(),
            fake.email(),
            dept,
            title,
            Decimal(base_salary),
            fake.ssn(),
            fake.date_of_birth(minimum_age=22, maximum_age=60),
        ))

    inserted_emps = await conn.fetch(
        """
        INSERT INTO employees
            (first_name, last_name, work_email, personal_email, department,
             title, salary, national_id, hire_date)
        SELECT * FROM UNNEST(
            $1::text[], $2::text[], $3::text[], $4::text[], $5::text[],
            $6::text[], $7::numeric[], $8::text[], $9::date[]
        )
        RETURNING id, title
        """,
        [r[0] for r in emp_rows],
        [r[1] for r in emp_rows],
        [r[2] for r in emp_rows],
        [r[3] for r in emp_rows],
        [r[4] for r in emp_rows],
        [r[5] for r in emp_rows],
        [r[6] for r in emp_rows],
        [r[7] for r in emp_rows],
        [r[8] for r in emp_rows],
    )

    employee_ids = [str(r["id"]) for r in inserted_emps]
    manager_ids = [
        str(r["id"]) for r in inserted_emps
        if any(kw in r["title"] for kw in ("Manager", "Director", "VP", "CFO", "CISO", "General"))
    ]

    # Assign managers
    if manager_ids:
        for emp_id in employee_ids:
            if emp_id not in manager_ids and random.random() > 0.2:
                mgr = random.choice(manager_ids)
                await conn.execute(
                    "UPDATE employees SET manager_id = $1 WHERE id = $2",
                    mgr, emp_id,
                )

    # ── customers ────────────────────────────────────────────
    print(f"Seeding {NUM_CUSTOMERS} customers …")
    cust_rows = []
    for _ in range(NUM_CUSTOMERS):
        cust_rows.append((
            fake.first_name(),
            fake.last_name(),
            fake.unique.email(),
            fake.phone_number()[:20],
            fake.date_of_birth(minimum_age=18, maximum_age=80),
            random_ssn(),
            fake.street_address(),
            fake.city(),
            fake.state_abbr(),
            fake.zipcode(),
            random.choice(["pending", "verified", "verified", "verified", "rejected"]),
        ))

    inserted_custs = await conn.fetch(
        """
        INSERT INTO customers
            (first_name, last_name, email, phone, date_of_birth, ssn,
             address_line1, address_city, address_state, address_zip, kyc_status)
        SELECT * FROM UNNEST(
            $1::text[], $2::text[], $3::text[], $4::text[], $5::date[],
            $6::text[], $7::text[], $8::text[], $9::char(2)[], $10::text[],
            $11::text[]
        )
        RETURNING id
        """,
        *[[r[i] for r in cust_rows] for i in range(11)],
    )
    customer_ids = [str(r["id"]) for r in inserted_custs]

    # ── accounts ─────────────────────────────────────────────
    print("Seeding accounts …")
    acct_rows = []
    account_ids: list[str] = []

    for cust_id in customer_ids:
        n = random.randint(*ACCTS_PER_CUST)
        used_types: set[str] = set()
        for _ in range(n):
            acct_type = random.choice(["checking", "savings", "credit", "investment"])
            while acct_type in used_types:
                acct_type = random.choice(["checking", "savings", "credit", "investment"])
            used_types.add(acct_type)

            balance = Decimal(random.uniform(-500, 150_000)).quantize(Decimal("0.01"))
            acct_rows.append((
                cust_id,
                random_account_number(),
                acct_type,
                balance,
                random.choice(["active", "active", "active", "frozen", "closed"]),
            ))

    inserted_accts = await conn.fetch(
        """
        INSERT INTO accounts (customer_id, account_number, account_type, balance, status)
        SELECT * FROM UNNEST(
            $1::uuid[], $2::text[], $3::text[], $4::numeric[], $5::text[]
        )
        RETURNING id, status
        """,
        [r[0] for r in acct_rows],
        [r[1] for r in acct_rows],
        [r[2] for r in acct_rows],
        [r[3] for r in acct_rows],
        [r[4] for r in acct_rows],
    )
    account_ids = [str(r["id"]) for r in inserted_accts if r["status"] == "active"]

    # ── transactions ─────────────────────────────────────────
    print("Seeding transactions …")
    txn_rows = []
    base_date = date.today() - timedelta(days=365)

    for acct_id in account_ids:
        n = random.randint(*TXN_PER_ACCT)
        for _ in range(n):
            merchant_name, merchant_raw = random.choice(MERCHANTS)
            txn_type = random.choice(["debit", "debit", "debit", "credit", "transfer", "fee", "refund"])
            amount = Decimal(random.uniform(1, 2000)).quantize(Decimal("0.01"))
            days_ago = random.randint(0, 365)
            ref_id = hashlib.md5(f"{acct_id}{_}{days_ago}".encode()).hexdigest()[:16]

            txn_rows.append((
                acct_id,
                amount,
                txn_type,
                merchant_name,
                merchant_raw,
                random_card_last4(),
                random.choice(["completed", "completed", "completed", "pending", "failed"]),
                ref_id,
                base_date + timedelta(days=days_ago),
            ))

    await conn.execute(
        """
        INSERT INTO transactions
            (account_id, amount, transaction_type, merchant_name, merchant_raw,
             card_last4, status, reference_id, created_at)
        SELECT * FROM UNNEST(
            $1::uuid[], $2::numeric[], $3::text[], $4::text[], $5::text[],
            $6::char(4)[], $7::text[], $8::text[], $9::timestamptz[]
        )
        """,
        [r[0] for r in txn_rows],
        [r[1] for r in txn_rows],
        [r[2] for r in txn_rows],
        [r[3] for r in txn_rows],
        [r[4] for r in txn_rows],
        [r[5] for r in txn_rows],
        [r[6] for r in txn_rows],
        [r[7] for r in txn_rows],
        [r[8] for r in txn_rows],
    )

    # ── summary ──────────────────────────────────────────────
    counts = await conn.fetchrow(
        """
        SELECT
            (SELECT COUNT(*) FROM customers)    AS customers,
            (SELECT COUNT(*) FROM accounts)     AS accounts,
            (SELECT COUNT(*) FROM transactions) AS transactions,
            (SELECT COUNT(*) FROM employees)    AS employees
        """
    )
    print("\n✓ Seed complete:")
    print(f"  customers    : {counts['customers']:>7,}")
    print(f"  accounts     : {counts['accounts']:>7,}")
    print(f"  transactions : {counts['transactions']:>7,}")
    print(f"  employees    : {counts['employees']:>7,}")


async def main() -> None:
    print(f"Connecting to {DATABASE_URL} …")
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await seed(conn)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
