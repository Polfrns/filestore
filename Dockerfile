FROM python:3.12-slim AS base

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

EXPOSE 8000

FROM base AS test
COPY requirements-dev.txt .
RUN pip install --no-cache-dir -r requirements-dev.txt
ENTRYPOINT []
CMD ["pytest"]

FROM base AS production
ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["uvicorn", "leedeo_filestore.asgi:application", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
