# Контекст сборки — корень репозитория, а не каталог telegram_panel.
FROM docker.io/library/node:22-alpine AS frontend-build
WORKDIR /build/frontend
COPY src/telegram_panel/frontend/package.json src/telegram_panel/frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY src/telegram_panel/frontend/ ./
RUN npm run build

# Полные зависимости нужны в том числе для /chart и импортов торгового ядра.
FROM docker.io/library/python:3.12-slim-bookworm AS dependencies
WORKDIR /build
COPY requirements.txt requirements.lock ./
COPY src/telegram_panel/requirements.txt ./panel-requirements.txt
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt -r panel-requirements.txt -c requirements.lock

FROM docker.io/library/python:3.12-slim-bookworm AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MPLBACKEND=Agg \
    MPLCONFIGDIR=/tmp/matplotlib \
    TZ=UTC \
    PANEL_PORT=8080 \
    PANEL_DATA_DIR=/app/data \
    PANEL_CHARTS_DIR=/app/charts \
    PANEL_CONFIG_PATH=/app/config/active.json \
    SERVO_DATA_DIR=/app/data
WORKDIR /app
COPY requirements.txt requirements.lock ./
COPY src/telegram_panel/requirements.txt ./panel-requirements.txt
COPY --from=dependencies /wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt -r panel-requirements.txt -c requirements.lock \
    && rm -rf /wheels \
    && mkdir -p data/prices data/news data/logs charts
COPY src/ ./src/
COPY config/ ./config/
COPY scripts/container_healthcheck.py ./scripts/container_healthcheck.py
COPY --from=frontend-build /build/frontend/dist ./frontend/dist
EXPOSE 8080
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "scripts/container_healthcheck.py", "panel"]
CMD ["python", "-m", "src.telegram_panel.run_panel"]
