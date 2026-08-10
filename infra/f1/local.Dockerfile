FROM python:3.11-slim@sha256:90744cff8f32887f075c47d747a173ff333e9e98801667af93c357fa9f5e28ff

WORKDIR /app

COPY requirements/requirements-f1.lock /app/requirements.txt
RUN pip install --require-hashes --no-cache-dir -r /app/requirements.txt

COPY src/ /app/src/
COPY migrations/ /app/migrations/
COPY alembic.ini /app/alembic.ini
COPY infra/f1/ /app/infra/f1/

ENV PYTHONPATH=/app/src

EXPOSE 8001
CMD ["python", "-m", "uvicorn", "platform_foundation.f1.api.main:app", "--host", "0.0.0.0", "--port", "8001"]
