FROM python:3.12-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY pyproject.toml .
RUN pip install --no-cache-dir -e ".[dev]"

# App source
COPY . .

EXPOSE 8000

# Fly.io overrides this per-process via fly.toml's [processes] block; this is
# just the default for a plain `docker run` / docker-compose without one.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
