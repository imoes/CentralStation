FROM node:22-alpine AS builder

ARG HTTP_PROXY=http://proxy.ippen.media:80
ARG HTTPS_PROXY=http://proxy.ippen.media:80
ARG NO_PROXY=localhost,127.0.0.1

ENV HTTP_PROXY=${HTTP_PROXY}
ENV HTTPS_PROXY=${HTTPS_PROXY}
ENV NO_PROXY=${NO_PROXY}

RUN npm config set proxy "${HTTP_PROXY}" && \
    npm config set https-proxy "${HTTPS_PROXY}"

WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build -- --configuration=production

FROM nginx:alpine
COPY --from=builder /app/dist/frontend/browser /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
