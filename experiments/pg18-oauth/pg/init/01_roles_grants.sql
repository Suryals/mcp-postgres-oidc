-- Three LOGIN roles (no passwords — authenticated via OAuth/Keycloak).
-- The whole point: column GRANTs make the DATABASE the boundary.
CREATE ROLE db_admin    LOGIN;
CREATE ROLE db_analyst  LOGIN;
CREATE ROLE db_readonly LOGIN;

CREATE TABLE customers (
    id          int PRIMARY KEY,
    first_name  text,
    ssn         text,
    email       text,
    balance     numeric
);
INSERT INTO customers VALUES
    (1, 'Joshua',  '692-19-1742', 'hmartin@example.org', 45230.00),
    (2, 'Katherine','694-89-2166','kphillips@example.net', 1200.50);

-- Column-level privileges — the security boundary moves into Postgres:
--   admin    : everything
--   analyst  : may read ssn (app shows partial mask) but NOT balance
--   readonly : may NOT read ssn or balance at all
GRANT SELECT                                  ON customers TO db_admin;
GRANT SELECT (id, first_name, ssn, email)     ON customers TO db_analyst;
GRANT SELECT (id, first_name, email)          ON customers TO db_readonly;
