#!/bin/sh
# Runs before the container's main process (see Dockerfile ENTRYPOINT).
set -e

echo "Running database migrations..."
python3 -m alembic upgrade head

echo "Training demo models..."
python3 scripts/train_demo_models.py

echo "Generating demo seller data..."
python3 scripts/generate_demo_sellers.py

echo "Seeding demo tenant (skips automatically if it already exists)..."
python3 scripts/seed_demo.py

exec "$@"
