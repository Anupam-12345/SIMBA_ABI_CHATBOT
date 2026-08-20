# ---- SOP Chatbot -----------------------------------------------------------
# Build:  docker compose build
# Run:    docker compose up -d
FROM python:3.12-slim

# Set INSTALL_IMAGE_TOOLS=true to add LibreOffice, which lets the indexer
# convert Word EMF/WMF images to PNG inside the container. Adds ~400 MB.
# Leave false and build the index on Windows if you want a smaller image.
ARG INSTALL_IMAGE_TOOLS=false

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates \
    && if [ "$INSTALL_IMAGE_TOOLS" = "true" ]; then \
         apt-get install -y --no-install-recommends libreoffice-draw fonts-dejavu; \
       fi \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

RUN chmod +x /app/docker-entrypoint.sh \
    && mkdir -p /app/vectorstore /app/database /app/docs

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD curl -fsS http://localhost:5000/login || exit 1

ENTRYPOINT ["/app/docker-entrypoint.sh"]
