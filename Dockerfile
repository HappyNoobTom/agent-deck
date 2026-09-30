FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/data/home \
    CODEX_HOME=/data/home/.codex \
    AGENT_DECK_STREAMDOCK_SDK_PATH=/app/vendor/streamdock-python-sdk/src

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libhidapi-libusb0 \
        libudev1 \
        fonts-wqy-zenhei \
        usbip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md LICENSE agent-deck.toml ./
COPY vendor ./vendor
COPY src ./src
COPY site ./site
COPY assets ./assets
COPY scripts/docker-entrypoint.sh ./scripts/docker-entrypoint.sh

RUN python -m pip install --no-cache-dir \
        "fastapi>=0.115.0" \
        "httpx>=0.28.0" \
        "pillow>=10.0.0" \
        "pydantic>=2.7.0" \
        "typer>=0.12.0" \
        "uvicorn>=0.30.0" \
    && python -m pip install --no-cache-dir ./vendor/streamdock-python-sdk \
    && python -m pip install --no-cache-dir --no-deps . \
    && chmod +x /app/scripts/docker-entrypoint.sh \
    && mkdir -p /data/home /data/state /data/logs

EXPOSE 8765
ENTRYPOINT ["/app/scripts/docker-entrypoint.sh"]
