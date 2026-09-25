#!/bin/sh
set -e

echo "Running database migrations..."
python3 -m alembic upgrade head

(
  echo "Training demo models..."
  python3 -u scripts/train_demo_models.py
  echo "Generating demo seller data..."
  python3 -u scripts/generate_demo_sellers.py
  echo "Seeding demo tenant..."
  python3 -u scripts/seed_demo.py
  echo "Demo setup complete."
) &

exec "$@"
