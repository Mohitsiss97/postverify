FROM python:3.11-slim

# Instagram, Facebook aur LinkedIn ke liye browser chahiye — unka content
# server-side aata hi nahi. Sirf X/YouTube chalane hain to ye layer nikal do
# aur PLATFORMS=x,youtube set kar do; image ~400MB chhoti ho jayegi.
RUN apt-get update && apt-get install -y --no-install-recommends \
        chromium fonts-liberation libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
ENV CHROME_PATH=/usr/bin/chromium

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
EXPOSE 8001
# Host apna PORT deta hai (Cloud Run, Render, Railway) — usi pe suno.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8001}"]
