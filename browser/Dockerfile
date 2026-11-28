# Headless Chromium for the board source curator. No data volume, no secrets, and no direct internet:
# it sits on internal Docker networks and every request goes through the public-only egress proxy.
FROM python:3.12-slim-bookworm
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONUNBUFFERED=1 PLAYWRIGHT_BROWSERS_PATH=/ms-playwright HOME=/tmp
RUN pip install playwright==1.49.1 && playwright install --with-deps chromium && rm -rf /var/lib/apt/lists/*
RUN useradd --system --uid 10001 --home-dir /tmp browser
COPY server.py /app/server.py
USER browser
EXPOSE 9300
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:9300/health')"
CMD ["python", "/app/server.py"]
