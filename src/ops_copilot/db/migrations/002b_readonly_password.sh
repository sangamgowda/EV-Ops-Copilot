#!/bin/bash
# Sets the read-only role's password from the environment.
#
# A password written into 002_readonly_role.sql would have to be kept
# in step with DB_READONLY_PASSWORD in .env by hand, and the day they
# drift every SQL tool call fails with "password authentication
# failed". Reading it here makes .env the single place it is set.
#
# Runs after 002_readonly_role.sql because initdb applies files in
# name order. Fails the whole init if the variable is missing, rather
# than leaving a role nobody can log in as.
set -euo pipefail

psql -v ON_ERROR_STOP=1 \
     --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
     -v ro_password="$DB_READONLY_PASSWORD" <<'EOSQL'
ALTER ROLE copilot_readonly PASSWORD :'ro_password';
EOSQL
