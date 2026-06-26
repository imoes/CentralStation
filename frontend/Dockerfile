FROM node:22-alpine AS builder

# Proxy build-args (empty by default — supplied via docker-compose.override.yml).
ARG HTTP_PROXY=""
ARG HTTPS_PROXY=""
ARG NO_PROXY=""

ENV HTTP_PROXY=${HTTP_PROXY}
ENV HTTPS_PROXY=${HTTPS_PROXY}
ENV NO_PROXY=${NO_PROXY}

RUN if [ -n "${HTTP_PROXY}" ]; then \
        npm config set proxy "${HTTP_PROXY}" && \
        npm config set https-proxy "${HTTPS_PROXY}"; \
    fi

WORKDIR /app
COPY package*.json ./
RUN npm install
COPY . .
RUN npm run build -- --configuration=production

FROM nginx:alpine
COPY --from=builder /app/dist/frontend/browser /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
