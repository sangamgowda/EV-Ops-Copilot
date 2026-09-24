#!/bin/bash
# Sets the read-only role's password from the environment.
#
# A password written into 002_readonly_role.sql would have to be kept
# in step with DB_READONLY_PASSWORD in .env by hand, and the day they
# drift every SQL tool call fails with "password authentication
# failed". Reading it here makes .env the single place it is set.
#
# Numbered 004 so it runs after 002 creates the role. The initdb
# entrypoint sorts files by locale collation, which ignores "_", so a
# name like "002b_..." sorts BEFORE "002_readonly_role" ("b" < "r").
# A distinct leading number is the only ordering that is unambiguous.
#
# Fails the whole init if the variable is missing, rather than leaving
# a role nobody can log in as.
set -euo pipefail

psql -v ON_ERROR_STOP=1 \
     --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
     -v ro_password="$DB_READONLY_PASSWORD" <<'EOSQL'
-- If this statement fails, Postgres logs it verbatim — password
-- included. Suppress statement logging for this session only.
SET log_min_error_statement = 'PANIC';
ALTER ROLE copilot_readonly PASSWORD :'ro_password';
EOSQL
