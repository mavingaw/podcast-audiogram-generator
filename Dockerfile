FROM node:24-bookworm-slim AS frontend
WORKDIR /src/frontend
COPY frontend/package*.json ./
RUN npm install
COPY frontend ./
RUN npm run build

FROM python:3.12-slim-bookworm AS backend
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PAS_CONFIG_DIR=/config
ENV PAS_DATA_DIR=/data
ENV PAS_FRONTEND_DIST=/app/frontend/dist

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY --from=frontend /src/frontend/dist ./frontend/dist

RUN mkdir -p /config /data \
    && useradd --uid 568 --user-group --home-dir /app --no-create-home pas \
    && chown -R pas:pas /app /config /data

USER pas
EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]

