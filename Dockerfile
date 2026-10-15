# Лёгкий образ: только headless-сборка Chromium (без Firefox/WebKit и окна браузера).
# Сервис всегда работает с HEADLESS=1; для отладки с окном — локальный venv, см. README.
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    DATA_DIR=/data

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install --with-deps --only-shell chromium \
    && rm -rf /var/lib/apt/lists/* /root/.cache

COPY app ./app

VOLUME ["/data"]
ENTRYPOINT ["python", "-m", "app.main"]
CMD ["run"]
