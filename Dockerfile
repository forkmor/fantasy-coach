FROM node:22-alpine AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/backend \
    HARNESS_DATA_DIR=/data \
    HARNESS_PORT=8765 \
    HARNESS_NO_BROWSER=1
WORKDIR /app
COPY backend/requirements-runtime.txt /app/backend/requirements-runtime.txt
RUN pip install --no-cache-dir -r /app/backend/requirements-runtime.txt
COPY backend/app /app/backend/app
COPY --from=frontend /build/frontend/dist /app/frontend/dist
RUN groupadd --gid 10001 fieldhouse \
    && useradd --uid 10001 --gid fieldhouse --no-create-home fieldhouse \
    && mkdir -p /data \
    && chown -R fieldhouse:fieldhouse /app /data
USER fieldhouse
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=3)"
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8765", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips=*", "--no-access-log"]
