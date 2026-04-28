# ─── Стадия 1: сборка зависимостей ───────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# Системные зависимости для компиляции (lxml, некоторые пакеты)
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libxml2-dev \
        libxslt-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ─── Стадия 2: финальный образ ────────────────────────────────────────────────
FROM python:3.11-slim

WORKDIR /app

# Копируем установленные пакеты из builder-стадии
COPY --from=builder /install /usr/local

# Копируем исходный код
COPY bot/ /app/bot/

# Переменные окружения по умолчанию (переопределяются через .env или docker-compose)
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    FIREBASE_CREDENTIALS_PATH=/app/secrets/firebase_credentials.json

# Healthcheck: просто проверяем что процесс жив
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import sys; sys.exit(0)"

CMD ["python", "bot/main.py"]
