# syntax=docker/dockerfile:1
# Targets: base (system tools) -> dev (hot reload, test deps) -> runtime (default, non-root).

FROM python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
# Tools the Bash tool and the agent commonly rely on.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl wget jq git ripgrep dnsutils iputils-ping netcat-openbsd whois nmap \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml ./
COPY app ./app

FROM base AS dev
RUN pip install -e ".[dev]"
COPY tests ./tests
ENV DATA_DIR=/data
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --start-period=15s --retries=6 \
  CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload", "--reload-dir", "app"]

FROM base AS runtime
RUN pip install -e . \
 && useradd --create-home --uid 1000 app \
 && mkdir -p /data \
 && chown -R app:app /data /app
ENV DATA_DIR=/data
USER app
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --start-period=15s --retries=6 \
  CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
