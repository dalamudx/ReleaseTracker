FROM node:22.22-alpine AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package*.json frontend/.npmrc ./
RUN --mount=type=cache,target=/root/.npm npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-alpine
LABEL org.opencontainers.image.description="A lightweight, configurable release tracking and update orchestration tool" \
      org.opencontainers.image.authors="dalamudx"
WORKDIR /app/backend
ARG DBMATE_VERSION=2.32.0
ARG DBMATE_SHA256=3b92413ef9b7d9b86981a39f8d0dbbaf03e0a07fc8db256480db6ab55915a787
ARG HELM_VERSION=3.17.3
ARG HELM_SHA256=ee88b3c851ae6466a3de507f7be73fe94d54cbf2987cbaa3d1a3832ea331f2cd
ARG UV_VERSION=0.9.21
ENV VIRTUAL_ENV=/app/backend/.venv
ENV PATH="/app/backend/.venv/bin:$PATH"
COPY backend/pyproject.toml backend/uv.lock ./
COPY backend/src ./src
COPY backend/dbmate ./dbmate
COPY backend/scripts ./scripts
COPY --from=frontend-builder /app/frontend/dist ./static
RUN --mount=type=cache,target=/root/.cache/pip \
    --mount=type=cache,target=/root/.cache/uv \
    apk add --no-cache --virtual .build-deps gcc musl-dev libffi-dev curl && \
    curl -fsSL -o /usr/local/bin/dbmate "https://github.com/amacneil/dbmate/releases/download/v${DBMATE_VERSION}/dbmate-linux-amd64" && \
    echo "${DBMATE_SHA256}  /usr/local/bin/dbmate" | sha256sum -c - && \
    curl -fsSL -o /tmp/helm.tar.gz "https://get.helm.sh/helm-v${HELM_VERSION}-linux-amd64.tar.gz" && \
    echo "${HELM_SHA256}  /tmp/helm.tar.gz" | sha256sum -c - && \
    tar -xz -C /tmp -f /tmp/helm.tar.gz && \
    mv /tmp/linux-amd64/helm /usr/local/bin/helm && \
    chmod +x /usr/local/bin/dbmate /usr/local/bin/helm && \
    chmod +x /app/backend/scripts/docker-entrypoint.sh && \
    pip install "uv==${UV_VERSION}" && \
    uv sync --locked --no-dev && \
    rm -rf /tmp/linux-amd64 /tmp/helm.tar.gz && \
    apk del .build-deps
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=60s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health/ready', timeout=2).close()" || exit 1
ENTRYPOINT ["/app/backend/scripts/docker-entrypoint.sh"]
CMD ["serve"]
