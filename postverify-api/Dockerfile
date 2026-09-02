FROM python:3.11-slim

# Instagram, Facebook and LinkedIn need a browser: their content never arrives
# server-side. If a deployment only needs X and YouTube, delete this layer and
# set PLATFORMS=x,youtube — the image shrinks by roughly 400 MB.
RUN apt-get update && apt-get install -y --no-install-recommends \
        chromium fonts-liberation libgl1 libglib2.0-0 curl \
    && rm -rf /var/lib/apt/lists/*
ENV CHROME_PATH=/usr/bin/chromium \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app

# Do not run as root: a break-out from the browser process should not land on a
# privileged account. Chromium needs a writable home for its profile
# directories, which is why one is created here.
RUN useradd --create-home --uid 10001 engine && chown -R engine:engine /srv
USER engine

EXPOSE 8000

# Liveness only. Readiness is /ready, which also reports whether the browser is
# present — an orchestrator must not restart the process over a missing Chrome,
# because restarting will not install it.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS "http://localhost:${PORT:-8000}/health" || exit 1

# The host supplies PORT on Cloud Run, Render and Railway; listen on it.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
