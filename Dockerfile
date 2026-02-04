FROM node:20-alpine AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-alpine
WORKDIR /app/backend
COPY backend/pyproject.toml ./
COPY backend/src ./src
COPY --from=frontend-builder /app/frontend/dist ./static
RUN apk add --no-cache gcc musl-dev libffi-dev && \
    pip install --no-cache-dir -e . && \
    apk del gcc musl-dev libffi-dev
EXPOSE 8000
CMD ["uvicorn", "releasetracker.main:app", "--host", "0.0.0.0", "--port", "8000"]
