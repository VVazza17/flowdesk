# DECISION: slim, not alpine. Alpine's musl libc breaks many prebuilt wheels (asyncpg,
# later numpy/pandas) and gains little. python:3.12-slim publishes amd64 and arm64.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

# Dependencies first: this layer is cached until requirements.txt changes.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY alembic.ini ./
COPY alembic ./alembic

RUN useradd --system --uid 10001 --no-create-home flowdesk
USER flowdesk

EXPOSE 8000

# slim has no curl; the stdlib is enough for a liveness probe.
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=2).status == 200 else 1)"]

# --no-access-log: CorrelationIdMiddleware emits the one per-request line (slice 5).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
