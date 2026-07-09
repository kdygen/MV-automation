# MV Automation backend — production image.
# Build:  docker build -t mv-backend ./backend
# Run:    docker run -p 8000:8000 --env-file .env mv-backend

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

# Install dependencies first for layer caching.
COPY pyproject.toml ./
COPY app ./app
COPY alembic.ini ./
COPY alembic ./alembic
RUN pip install --no-cache-dir .

# Run as an unprivileged user.
RUN useradd --create-home appuser
USER appuser

EXPOSE 8000

# Migrations are run as a deploy step (`alembic upgrade head`), not at container boot,
# to avoid concurrent-migration races when multiple replicas start.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
