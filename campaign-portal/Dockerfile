FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /srv

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Runtime requirements only; the test runner is not shipped to production.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY migrations ./migrations
COPY alembic.ini .

# Do not run as root: a break-in to the container then reaches an unprivileged
# account rather than the host's root.
RUN useradd --create-home --uid 10001 portal \
    && mkdir -p /srv/storage && chown -R portal:portal /srv
USER portal

EXPOSE 8000

# Liveness only. Readiness is /ready, which also checks the database and the
# engine — a load balancer should route on that, but an orchestrator must not
# restart the portal because a dependency of its own is down.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS "http://localhost:${PORT:-8000}/health" || exit 1

# The schema is changed by migrations, never by create_all. Running the
# migration in the entrypoint is fine for a single instance; see
# docs/deployment.md for the multi-instance case, where it must be a separate
# step so that concurrent instances do not race for the Alembic lock.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
