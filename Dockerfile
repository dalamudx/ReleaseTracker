FROM node:20-alpine AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN --mount=type=cache,target=/root/.npm npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-alpine
WORKDIR /app/backend
ARG DBMATE_VERSION=2.32.0
ARG HELM_VERSION=3.17.3
COPY backend/pyproject.toml ./
COPY backend/src ./src
COPY backend/dbmate ./dbmate
COPY backend/scripts ./scripts
COPY --from=frontend-builder /app/frontend/dist ./static
RUN --mount=type=cache,target=/root/.cache/pip \
    apk add --no-cache --virtual .build-deps gcc musl-dev libffi-dev curl && \
    curl -fsSL -o /usr/local/bin/dbmate "https://github.com/amacneil/dbmate/releases/download/v${DBMATE_VERSION}/dbmate-linux-amd64" && \
    curl -fsSL "https://get.helm.sh/helm-v${HELM_VERSION}-linux-amd64.tar.gz" | tar -xz -C /tmp && \
    mv /tmp/linux-amd64/helm /usr/local/bin/helm && \
    chmod +x /usr/local/bin/dbmate /usr/local/bin/helm && \
    chmod +x /app/backend/scripts/docker-entrypoint.sh && \
    pip install -e . && \
    rm -rf /tmp/linux-amd64 && \
    apk del .build-deps
EXPOSE 8000
ENTRYPOINT ["/app/backend/scripts/docker-entrypoint.sh"]
CMD ["serve"]
