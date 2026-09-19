#!/bin/sh
# Runs before the container's main process (see Dockerfile ENTRYPOINT).
#
# Without this, `docker-compose up` starts gunicorn directly against a
# freshly-created, empty Postgres database — every request touching the DB
# fails immediately because none of the tables exist yet, and there was
# previously no documented or automated step that ran `alembic upgrade
# head` anywhere in the container lifecycle. `alembic upgrade head` is
# idempotent (already-applied revisions are skipped), so running it on
# every container start is safe for this single-instance docker-compose
# setup.
#
# NOTE for multi-replica deployments (e.g. Kubernetes with replicas > 1):
# running migrations from every replica's startup is NOT safe in general
# (concurrent DDL from multiple pods racing on the same revision can
# contend or fail) — use a dedicated migration Job/initContainer that runs
# once before the Deployment rolls out instead of this entrypoint pattern.
set -e

echo "Running database migrations..."
python3 -m alembic upgrade head

exec "$@"
