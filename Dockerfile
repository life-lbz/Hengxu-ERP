FROM node:24-bookworm-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim
WORKDIR /srv
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY backend ./backend
COPY --from=frontend /build/dist ./frontend/dist
RUN useradd --system --create-home erp
USER erp
EXPOSE 8000
CMD ["sh", "-c", "python -m backend.app.migrate && exec uvicorn backend.app.main:app --host 0.0.0.0 --port 8000"]
