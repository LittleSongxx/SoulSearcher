# ============================================================
# Stage 1: Builder - install Python dependencies into a venv
# ============================================================
FROM python:3.11-slim-bookworm AS builder

WORKDIR /build

ARG PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_EXTRA_INDEX_URL=
ARG PIP_DEFAULT_TIMEOUT=1200
ARG PIP_RETRIES=10

ENV PIP_INDEX_URL=${PIP_INDEX_URL} \
    PIP_EXTRA_INDEX_URL=${PIP_EXTRA_INDEX_URL} \
    PIP_DEFAULT_TIMEOUT=${PIP_DEFAULT_TIMEOUT} \
    PIP_RETRIES=${PIP_RETRIES} \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN set -eux; \
    printf 'Acquire::Retries "5";\nAcquire::http::Timeout "30";\nAcquire::https::Timeout "30";\n' > /etc/apt/apt.conf.d/80-retries; \
    sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources; \
    for attempt in 1 2 3; do \
      apt-get update \
      && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
        git \
        libffi-dev \
        libpq-dev \
        pkg-config \
      && rm -rf /var/lib/apt/lists/* \
      && exit 0; \
      apt-get clean; \
      rm -rf /var/lib/apt/lists/*; \
      echo "apt install failed, retrying ($attempt/3)"; \
      sleep 5; \
    done; \
    exit 1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt requirements-dev.txt requirements-optional.txt ./
RUN pip install --no-cache-dir --upgrade pip setuptools wheel \
    && pip install --no-cache-dir -r requirements.txt -r requirements-dev.txt -r requirements-optional.txt

# ============================================================
# Stage 2: Runtime - app image with browser/system dependencies
# ============================================================
FROM python:3.11-slim-bookworm AS runtime

WORKDIR /app

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN set -eux; \
    printf 'Acquire::Retries "5";\nAcquire::http::Timeout "30";\nAcquire::https::Timeout "30";\n' > /etc/apt/apt.conf.d/80-retries; \
    sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources; \
    for attempt in 1 2 3; do \
      apt-get update \
      && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        fonts-liberation \
        fonts-noto-cjk \
        git \
        libasound2 \
        libatk-bridge2.0-0 \
        libatk1.0-0 \
        libcairo2 \
        libcups2 \
        libdbus-1-3 \
        libdrm2 \
        libexpat1 \
        libffi8 \
        libgbm1 \
        libgdk-pixbuf-2.0-0 \
        libglib2.0-0 \
        libgtk-3-0 \
        libnspr4 \
        libnss3 \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libpq5 \
        libu2f-udev \
        libx11-6 \
        libx11-xcb1 \
        libxcb1 \
        libxcomposite1 \
        libxdamage1 \
        libxext6 \
        libxfixes3 \
        libxkbcommon0 \
        libxrandr2 \
        libxshmfence1 \
        python3-tk \
        scrot \
        shared-mime-info \
        tini \
        xauth \
        xclip \
        xdg-utils \
        xvfb \
      && rm -rf /var/lib/apt/lists/* \
      && exit 0; \
      apt-get clean; \
      rm -rf /var/lib/apt/lists/*; \
      echo "apt install failed, retrying ($attempt/3)"; \
      sleep 5; \
    done; \
    exit 1

COPY --from=builder /opt/venv /opt/venv

RUN mkdir -p /ms-playwright \
    && python -m playwright install chromium \
    && groupadd --gid 1000 soulsearcher \
    && useradd --uid 1000 --gid soulsearcher --shell /bin/bash --create-home soulsearcher \
    && mkdir -p /app/logs /app/data /app/.docker-data \
    && chown -R soulsearcher:soulsearcher /app /ms-playwright

COPY --chown=soulsearcher:soulsearcher . .

USER soulsearcher

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=5 \
    CMD curl -f http://localhost:8000/health || exit 1

ENTRYPOINT ["tini", "--"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
