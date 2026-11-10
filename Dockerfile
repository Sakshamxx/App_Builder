# Hosted demo image (e.g. Render, Docker runtime). Runs as a non-root user. Chromium is installed for the trusted check harness.
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

# Dependencies first so this layer is cached when only the code changes.
COPY requirements.txt .
RUN pip install -r requirements.txt \
 && playwright install --with-deps chromium \
 && rm -rf /var/lib/apt/lists/*

# Non-root user; the app writes projects under /app/data (ephemeral on free hosting plans).
RUN useradd --create-home --uid 10001 app \
 && mkdir -p /app/data \
 && chown -R app:app /app
COPY --chown=app:app appbuilder ./appbuilder

USER app
ENV APPBUILDER_DATA_DIR=/app/data
EXPOSE 8080
CMD ["python", "-m", "appbuilder.server"]
