FROM python:3.11-slim

# Instagram ke liye browser chahiye — uska content server-side aata hi nahi.
# Sirf X/LinkedIn/YouTube chalane hain to ye layer nikal do aur image ~400MB
# chhoti ho jayegi (PLATFORMS env se services choose kar sakte ho).
RUN apt-get update && apt-get install -y --no-install-recommends \
        chromium fonts-liberation \
    && rm -rf /var/lib/apt/lists/*
ENV CHROME_PATH=/usr/bin/chromium

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
EXPOSE 8000
# Host apna PORT deta hai (Cloud Run, Render, Railway) — usi pe suno.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
