FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONOPTIMIZE=2 \
    MALLOC_ARENA_MAX=2

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ghostscript libzbar0 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-compile -r requirements.txt

COPY . .

RUN groupadd -r appuser && useradd -r -g appuser appuser \
    && mkdir -p /app/data /app/tmp \
    && chown -R appuser:appuser /app

USER appuser

ENV DATA_DIR=/app/data \
    TMP_DIR=/app/tmp

HEALTHCHECK --interval=60s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c 'import os,urllib.request; urllib.request.urlopen("https://api.telegram.org/bot" + os.environ["BOT_TOKEN"] + "/getMe", timeout=8)' || exit 1

CMD ["python", "bot.py"]
