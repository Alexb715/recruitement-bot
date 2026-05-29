# syntax=docker/dockerfile:1

# Pinned to match the project's Python version (see requirements.txt / .venv).
FROM python:3.14-slim

# Container-friendly Python: no .pyc files, unbuffered stdout/stderr so logs
# show up immediately in `docker logs`.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies first so this layer stays cached unless requirements.txt
# changes.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application source (see .dockerignore for what's excluded).
COPY . .

# Run as an unprivileged user. /app/data holds the SQLite database, which lines
# up with the default DB_PATH=data/applications.db (relative to /app); mount a
# volume there to persist applications across restarts.
RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app
USER appuser

VOLUME ["/app/data"]

CMD ["python", "bot.py"]
