# F1.1 web: multi-stage build (npm ci, no dist mount, digest-pinned bases).
ARG F111_SOURCE_SNAPSHOT_SHA256
ARG F111_DOCKERFILE_SET_SHA256
ARG F111_PYTHON_LOCK_SHA256
ARG F111_NPM_LOCK_SHA256

# Stage 1: build the Vite React app with a pinned Node base.
FROM node:22-alpine@sha256:c610fcdfb1d5b4740dd70c284ed3cb16bb857e0f7166196e36a5501df7a3aa32 AS build
ARG ANHUAN_PWA_UPDATE_PROBE=""
WORKDIR /app
COPY src/web/package.json src/web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY src/web/ ./
RUN if [ -n "${ANHUAN_PWA_UPDATE_PROBE}" ]; then \
      test "${#ANHUAN_PWA_UPDATE_PROBE}" -eq 24; \
      case "${ANHUAN_PWA_UPDATE_PROBE}" in *[!0-9a-f]*) exit 64 ;; esac; \
      printf '%s\n' "${ANHUAN_PWA_UPDATE_PROBE}" > public/pwa-update-probe.txt; \
    fi \
    && npm run build \
    && node ./scripts/inject-pwa-build-id.mjs ./dist

# Stage 2: serve the built static assets with a pinned nginx base.
FROM nginx:1.27-alpine@sha256:65645c7bb6a0661892a8b03b89d0743208a18dd2f3f17a54ef4b76fb8e2f2a10
ARG F111_SOURCE_SNAPSHOT_SHA256
ARG F111_DOCKERFILE_SET_SHA256
ARG F111_PYTHON_LOCK_SHA256
ARG F111_NPM_LOCK_SHA256
ARG ANHUAN_ENGINEERING_PROJECT_ID=""
ARG ANHUAN_PWA_UPDATE_PROBE=""

LABEL org.opencontainers.image.revision="${F111_SOURCE_SNAPSHOT_SHA256}" \
      io.anhuan.f111.source-snapshot-sha256="${F111_SOURCE_SNAPSHOT_SHA256}" \
      io.anhuan.f111.dockerfile-set-sha256="${F111_DOCKERFILE_SET_SHA256}" \
      io.anhuan.f111.python-lock-sha256="${F111_PYTHON_LOCK_SHA256}" \
      io.anhuan.f111.npm-lock-sha256="${F111_NPM_LOCK_SHA256}" \
      io.anhuan.pwa-update-project-id="${ANHUAN_ENGINEERING_PROJECT_ID}" \
      io.anhuan.pwa-update-probe="${ANHUAN_PWA_UPDATE_PROBE}"
COPY infra/f1/nginx/default.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
