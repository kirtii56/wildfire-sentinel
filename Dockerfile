FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies first so this layer is cached between code changes.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY migrations ./migrations
COPY scripts ./scripts

# Run as a non-root user.
RUN useradd --create-home appuser
USER appuser

EXPOSE 8000

# Apply any pending migrations, then start the API.
CMD ["sh", "-c", "python scripts/migrate.py && uvicorn app.api.main:app --host 0.0.0.0 --port 8000"]
