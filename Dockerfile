FROM node:20-alpine AS frontend-builder
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
ARG HELM_VERSION=3.17.3
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
    curl -fsSL "https://get.helm.sh/helm-v${HELM_VERSION}-linux-amd64.tar.gz" | tar -xz -C /tmp && \
    mv /tmp/linux-amd64/helm /usr/local/bin/helm && \
    chmod +x /usr/local/bin/dbmate /usr/local/bin/helm && \
    chmod +x /app/backend/scripts/docker-entrypoint.sh && \
    pip install "uv==${UV_VERSION}" && \
    uv sync --locked --no-dev && \
    rm -rf /tmp/linux-amd64 && \
    apk del .build-deps
EXPOSE 8000
ENTRYPOINT ["/app/backend/scripts/docker-entrypoint.sh"]
CMD ["serve"]
