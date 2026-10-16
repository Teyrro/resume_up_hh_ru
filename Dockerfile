# Полный Chromium (без Firefox/WebKit) в новом headless-режиме: в отличие от облегчённой
# headless-сборки у него настоящие плагины, navigator.userAgentData и заголовки Sec-CH-UA.
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    DATA_DIR=/data

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install --with-deps --no-shell chromium \
    && rm -rf /var/lib/apt/lists/* /root/.cache

COPY app ./app

VOLUME ["/data"]
ENTRYPOINT ["python", "-m", "app.main"]
CMD ["run"]
