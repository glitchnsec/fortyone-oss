# Stage 1: Build the React dashboard
FROM node:20-slim AS dashboard-build

WORKDIR /dashboard
COPY dashboard/package.json dashboard/package-lock.json* ./
RUN npm install
COPY dashboard/ .
RUN npm run build

# Stage 2: Python API + built dashboard
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Copy built dashboard into the location FastAPI serves from
COPY --from=dashboard-build /dashboard/dist /app/dashboard/dist

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
