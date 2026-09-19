# Multi-stage build: keeps the final image free of build tooling and source
# artifacts that aren't needed at runtime.

FROM python:3.12-slim AS builder

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt

FROM python:3.12-slim

# Non-root runtime user — never run the app as root in a container.
RUN groupadd --gid 1000 appuser && useradd --uid 1000 --gid appuser --shell /bin/bash --create-home appuser

WORKDIR /app

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

COPY . .

# Directories the app writes to at runtime — created here so they exist
# with the right ownership before the app ever tries to use them.
RUN mkdir -p data/dataset_uploads model_registry \
    && chown -R appuser:appuser /app \
    && chmod +x docker-entrypoint.sh

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:${PORT:-8000}/healthz')" || exit 1

ENTRYPOINT ["./docker-entrypoint.sh"]

CMD gunicorn api.main:app -k uvicorn.workers.UvicornWorker -w ${WEB_CONCURRENCY:-1} --bind 0.0.0.0:${PORT:-8000} --access-logfile - --error-logfile -
