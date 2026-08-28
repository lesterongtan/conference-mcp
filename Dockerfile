FROM python:3.12-slim AS builder

WORKDIR /build
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY alembic.ini ./
COPY migrations ./migrations
RUN pip wheel --no-cache-dir --wheel-dir /wheels .

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CONFERENCE_SCOUT_HTTP_HOST=0.0.0.0 \
    CONFERENCE_SCOUT_HTTP_PORT=8765 \
    CONFERENCE_SCOUT_REQUIRE_AUTH=true \
    CONFERENCE_SCOUT_AUTO_CREATE_SCHEMA=false \
    CONFERENCE_SCOUT_EMBEDDED_WORKER=false

RUN groupadd --system conference-scout \
    && useradd --system --gid conference-scout --create-home conference-scout
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/* && rm -rf /wheels

USER conference-scout
WORKDIR /app
COPY --from=builder /build/alembic.ini /app/alembic.ini
COPY --from=builder /build/migrations /app/migrations
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/readyz', timeout=3)"

CMD ["conference-scout-api"]
