# F1.1 API / Worker image (host-independent).
# The API/worker reach the shared fixture services (postgres/keycloak/minio/
# redis/ragflow) via env-overridable host config; the secrets and the F0-I
# key are mounted read-only at runtime.
FROM python:3.11-slim@sha256:90744cff8f32887f075c47d747a173ff333e9e98801667af93c357fa9f5e28ff

ARG F111_SOURCE_SNAPSHOT_SHA256
ARG F111_DOCKERFILE_SET_SHA256
ARG F111_PYTHON_LOCK_SHA256
ARG F111_NPM_LOCK_SHA256

LABEL org.opencontainers.image.revision="${F111_SOURCE_SNAPSHOT_SHA256}" \
      io.anhuan.f111.source-snapshot-sha256="${F111_SOURCE_SNAPSHOT_SHA256}" \
      io.anhuan.f111.dockerfile-set-sha256="${F111_DOCKERFILE_SET_SHA256}" \
      io.anhuan.f111.python-lock-sha256="${F111_PYTHON_LOCK_SHA256}" \
      io.anhuan.f111.npm-lock-sha256="${F111_NPM_LOCK_SHA256}"

WORKDIR /app

# Install pinned deps (local only — no index access needed).
COPY requirements/requirements-f1.lock /app/requirements.txt
RUN pip install --require-hashes --no-cache-dir -r /app/requirements.txt

COPY src/ /app/src/
COPY infra/f1/migrate_f1.py /app/infra/f1/migrate_f1.py
COPY infra/f1/alembic /app/infra/f1/alembic
COPY infra/f1/alembic.ini /app/infra/f1/alembic.ini
COPY migrations/ /app/migrations/
COPY alembic.ini /app/alembic.ini

ENV PYTHONPATH=/app/src
ENV F1_PG_HOST=host.docker.internal \
    F1_PG_PORT=55432 \
    KEYCLOAK_URL=http://host.docker.internal:8080 \
    MINIO_ENDPOINT=host.docker.internal:9000 \
    REDIS_URL=redis://host.docker.internal:6379/0 \
    RAGFLOW_BASE_URL=http://host.docker.internal:80

EXPOSE 8001
CMD ["python", "-m", "uvicorn", "platform_foundation.f1.api.main:app", "--host", "0.0.0.0", "--port", "8001"]
