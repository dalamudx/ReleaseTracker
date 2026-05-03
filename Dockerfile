FROM node:20-alpine AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-alpine
WORKDIR /app/backend
ARG DBMATE_VERSION=2.32.0
COPY backend/pyproject.toml ./
COPY backend/src ./src
COPY backend/dbmate ./dbmate
COPY backend/scripts ./scripts
COPY --from=frontend-builder /app/frontend/dist ./static
RUN apk add --no-cache gcc musl-dev libffi-dev curl && \
    curl -fsSL -o /usr/local/bin/dbmate "https://github.com/amacneil/dbmate/releases/download/v${DBMATE_VERSION}/dbmate-linux-amd64" && \
    chmod +x /usr/local/bin/dbmate && \
    chmod +x /app/backend/scripts/docker-entrypoint.sh && \
    pip install --no-cache-dir -e . && \
    apk del gcc musl-dev libffi-dev curl
EXPOSE 8000
ENTRYPOINT ["/app/backend/scripts/docker-entrypoint.sh"]
CMD ["serve"]
