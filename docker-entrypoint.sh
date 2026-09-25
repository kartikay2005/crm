#!/bin/sh
# Runs before the container's main process (see Dockerfile ENTRYPOINT).
set -e

echo "Running database migrations..."
python3 -m alembic upgrade head

# Model training, demo seller data, and demo-tenant seeding run in the
# background instead of blocking here — Render fails a deploy if nothing
# binds a port within its scan window, and this setup can take longer
# than that window allows on a free-tier CPU.
(
  echo "Training demo models..."
  python3 scripts/train_demo_models.py
  echo "Generating demo seller data..."
  python3 scripts/generate_demo_sellers.py
  echo "Seeding demo tenant..."
  python3 scripts/seed_demo.py
  echo "Demo setup complete."
) &

exec "$@"
