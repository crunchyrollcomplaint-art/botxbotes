# Stage 1: compile the official Telegram Local Bot API server.
FROM ubuntu:24.04 AS telegram-api-builder

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates cmake g++ git gperf make libssl-dev zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
RUN git clone --recursive --depth 1 https://github.com/tdlib/telegram-bot-api.git
WORKDIR /src/telegram-bot-api
RUN cmake -S . -B build -DCMAKE_BUILD_TYPE=Release \
    && cmake --build build --target telegram-bot-api -j2 \
    && install -m 0755 build/telegram-bot-api /usr/local/bin/telegram-bot-api

# Stage 2: Python bot plus FFmpeg/FFprobe.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=180 \
    PIP_RETRIES=10 \
    PORT=10000 \
    LOCAL_BOT_API=true \
    TELEGRAM_API_BASE_URL=http://127.0.0.1:8081

WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY --from=telegram-api-builder /usr/local/bin/telegram-bot-api /usr/local/bin/telegram-bot-api
COPY requirements.txt ./
RUN pip install --no-cache-dir --retries 10 --timeout 180 --prefer-binary -r requirements.txt

COPY . .
RUN mkdir -p /tmp/telegram-video-compressor /tmp/telegram-bot-api-data /tmp/telegram-bot-api-temp \
    && useradd --create-home --uid 10001 botuser \
    && chown -R botuser:botuser /app /tmp/telegram-video-compressor /tmp/telegram-bot-api-data /tmp/telegram-bot-api-temp

USER botuser
EXPOSE 10000
CMD ["python", "main.py"]
