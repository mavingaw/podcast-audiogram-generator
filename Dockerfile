# syntax=docker/dockerfile:1.7

# ---------------------------------------------------------------------------
# Frontend build
# ---------------------------------------------------------------------------
FROM node:24-bookworm-slim AS frontend
WORKDIR /src/frontend

# Copy the manifests alone first so the dependency layer survives source edits.
COPY frontend/package.json frontend/package-lock.json ./
# `npm ci` installs exactly the locked tree and fails if the lockfile drifted,
# which is the difference between a reproducible image and a lucky one.
RUN --mount=type=cache,target=/root/.npm npm ci

COPY frontend/ ./
RUN npm run build


# ---------------------------------------------------------------------------
# Python dependencies
# ---------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS deps
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY backend/requirements.txt ./requirements.txt
# A virtualenv copies into the runtime stage as one self-contained directory,
# which keeps build toolchains out of the shipped image.
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt


# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PAS_CONFIG_DIR=/config \
    PAS_DATA_DIR=/data \
    PAS_FRONTEND_DIST=/app/frontend/dist

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        ca-certificates \
        # FFmpeg's drawtext needs a real font file to render the scene's text
        # layers; without one those layers are silently dropped.
        fonts-dejavu-core \
        # Reaps the zombies FFmpeg subprocesses would otherwise leave behind and
        # forwards signals, so `docker stop` is a clean shutdown rather than a
        # ten-second wait followed by SIGKILL.
        tini \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=deps /opt/venv /opt/venv

WORKDIR /app
COPY backend/app ./app
COPY --from=frontend /src/frontend/dist ./frontend/dist

# 568 is the conventional unprivileged service account on Unraid, so the bind
# mounts line up with what the host expects to own.
RUN useradd --uid 568 --user-group --home-dir /app --no-create-home pas \
    && mkdir -p /config /data \
    && chown -R pas:pas /app /config /data

USER pas
EXPOSE 8080
VOLUME ["/config", "/data"]

# Uses the app's own readiness endpoint: a listening socket is not the same as a
# usable database.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8080/api/health || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
# One worker on purpose: the job worker is an in-process thread holding the
# SQLite write lock, and a second worker would contend with it.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "*"]
