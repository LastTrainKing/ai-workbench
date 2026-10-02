# AI Workbench — frontend image (static build served by Nginx)

FROM node:22-alpine AS build

WORKDIR /app

# Install dependencies first so the layer is cached across source edits.
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund

COPY frontend/ ./

# The browser talks to the API through the Nginx reverse proxy at /api,
# so the build does not need to know the backend hostname.
ENV VITE_API_BASE_URL=/api/v1
RUN npm run build


FROM nginx:1.27-alpine AS runtime

COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
