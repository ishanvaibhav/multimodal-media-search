# AI Media Hub — API / worker image (slim, no ML stack).
# For the ML worker image see Dockerfile.worker (Phase 5).
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /srv

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY alembic/ ./alembic/
COPY alembic.ini ./

EXPOSE 8000

# Migrations run at container start; production deployments can instead run
# `alembic upgrade head` as a separate migration job.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
