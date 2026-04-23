FROM node:20-bookworm-slim

WORKDIR /app

ARG PNPM_VERSION=10.33.0

ENV NEXT_TELEMETRY_DISABLED=1 \
    PNPM_HOME=/usr/local/share/pnpm

ENV PATH="${PNPM_HOME}:${PATH}"

RUN npm install -g "pnpm@${PNPM_VERSION}" \
    && mkdir -p /app "$PNPM_HOME"

COPY package.json pnpm-lock.yaml ./
COPY docker-dev-entrypoint.sh /usr/local/bin/docker-dev-entrypoint.sh

RUN chmod +x /usr/local/bin/docker-dev-entrypoint.sh

EXPOSE 3100

ENTRYPOINT []
CMD ["/bin/sh", "/usr/local/bin/docker-dev-entrypoint.sh"]
