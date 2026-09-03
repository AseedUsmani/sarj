FROM python:3.11-slim

WORKDIR /app

# Dependencies first: this layer caches across code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY static/ ./static/

ENV PORT=8080 PYTHONUNBUFFERED=1
EXPOSE 8080

# Single worker: the workload is I/O-bound and the cache index is in-process,
# so scaling is a shared-index problem, not a worker-count one (docs/TDD.md §13).
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
