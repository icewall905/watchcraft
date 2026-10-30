# --- frontend build
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- runtime
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg tzdata poppler-utils && rm -rf /var/lib/apt/lists/*
# yt-dlp needs a JS runtime to solve YouTube player challenges
COPY --from=denoland/deno:bin-2.9.6 /deno /usr/local/bin/deno
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    BT_DATA_DIR=/data BT_MEDIA_DIR=/media BT_STATIC_DIR=/app/static
WORKDIR /app
COPY backend/requirements.txt ./
RUN pip install -r requirements.txt
COPY backend/bt ./bt
COPY --from=web /web/build ./static
COPY docker-entrypoint.sh /usr/local/bin/
VOLUME /data
EXPOSE 6645
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["uvicorn", "bt.main:app", "--host", "0.0.0.0", "--port", "6645", "--proxy-headers", "--forwarded-allow-ips", "*"]
