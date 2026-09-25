#!/bin/sh
# Runs before the container's main process (see Dockerfile ENTRYPOINT).
# Deliberately migrations-only: see services/demo_seed.py's module
# docstring for why demo-data seeding runs via the /_seed-demo HTTP
# endpoint instead of as a separate process started here.
set -e

echo "Running database migrations..."
python3 -m alembic upgrade head

exec "$@"
