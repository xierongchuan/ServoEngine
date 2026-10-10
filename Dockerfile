# Зависимости собираются на целевой архитектуре; хостовые бинарники не копируются.
FROM docker.io/library/python:3.12-slim-bookworm AS dependencies
WORKDIR /build
COPY requirements.txt requirements.lock ./
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt -c requirements.lock

FROM docker.io/library/python:3.12-slim-bookworm AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MPLBACKEND=Agg \
    MPLCONFIGDIR=/tmp/matplotlib \
    TZ=UTC
WORKDIR /app
COPY requirements.txt requirements.lock ./
COPY --from=dependencies /wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt -c requirements.lock \
    && rm -rf /wheels \
    && mkdir -p data/prices data/news data/logs charts
COPY src/ ./src/
COPY config/ ./config/
COPY run.py ./
COPY scripts/container_healthcheck.py ./scripts/container_healthcheck.py
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "scripts/container_healthcheck.py", "bot"]
CMD ["python", "run.py"]
