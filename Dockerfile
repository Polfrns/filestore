FROM node:24-bookworm-slim AS runtime

ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv bubblewrap ca-certificates \
       libreoffice-writer libreoffice-calc libreoffice-impress ffmpeg \
       tesseract-ocr tesseract-ocr-eng tesseract-ocr-spa tesseract-ocr-cat tesseract-ocr-fra \
       tesseract-ocr-por tesseract-ocr-deu tesseract-ocr-ita pandoc graphviz \
       libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --include=optional --no-fund --no-update-notifier \
    && rm -rf "$(npm config get cache)"
COPY src/ ./src/
COPY scripts/ ./scripts/
RUN node scripts/setup-files.mjs \
    && mkdir -p /data \
    && chown -R node:node /data /opt/roko-workspace-venv

ENV WORKSPACE_DATA_DIR=/data WORKSPACE_HOST=0.0.0.0 PORT=8080 \
    PATH=/opt/roko-workspace-venv/bin:$PATH
USER node
CMD ["node", "src/main.mjs"]

FROM runtime AS test
COPY --chown=node:node test/ ./test/
COPY --chown=node:node .env.example ./
CMD ["npm", "test"]
