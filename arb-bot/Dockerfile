FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY pyproject.toml README.md ./
COPY arbbot ./arbbot
RUN pip install ".[intl]"

COPY alembic.ini ./
COPY database ./database

RUN useradd --create-home --uid 10001 arbbot && mkdir -p /data && chown arbbot /data
USER arbbot
VOLUME /data

# Apply migrations, then start. The bot reconciles with both exchanges before it scans.
CMD ["sh", "-c", "alembic upgrade head && exec python -m arbbot"]
