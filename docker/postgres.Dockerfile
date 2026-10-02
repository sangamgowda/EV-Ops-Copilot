# Postgres 16 with pgvector (document search) AND TimescaleDB (telemetry).
#
# Built on the pgvector image the stack already used, adding TimescaleDB
# from Timescale's own Debian repository. Same Postgres major version and
# data directory, so an existing data volume starts as-is; migration 0010
# then turns vehicle_telemetry into a hypertable in place.
FROM pgvector/pgvector:pg16

# Pinned: an extension upgrade is a migration (ALTER EXTENSION ... UPDATE), not a rebuild side effect.
ARG TIMESCALEDB_VERSION=2.30.2~debian12-1615

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl gnupg \
 && . /etc/os-release \
 && curl -fsSL https://packagecloud.io/timescale/timescaledb/gpgkey \
      | gpg --dearmor -o /usr/share/keyrings/timescaledb.gpg \
 && echo "deb [signed-by=/usr/share/keyrings/timescaledb.gpg] https://packagecloud.io/timescale/timescaledb/debian/ ${VERSION_CODENAME} main" \
      > /etc/apt/sources.list.d/timescaledb.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
      timescaledb-2-postgresql-16=${TIMESCALEDB_VERSION} \
      timescaledb-2-loader-postgresql-16=${TIMESCALEDB_VERSION} \
 && apt-get purge -y --auto-remove curl gnupg \
 && rm -rf /var/lib/apt/lists/*
