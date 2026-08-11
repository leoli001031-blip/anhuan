ARG LOCAL_RUNTIME_IMAGE

FROM node:22-alpine@sha256:c610fcdfb1d5b4740dd70c284ed3cb16bb857e0f7166196e36a5501df7a3aa32 AS node_toolchain

WORKDIR /app/src/web
COPY src/web/package.json src/web/package-lock.json ./
RUN npm ci --no-audit --no-fund

FROM ${LOCAL_RUNTIME_IMAGE}

COPY --from=node_toolchain /lib/ /opt/anhuan-node/lib/
COPY --from=node_toolchain /usr/lib/ /opt/anhuan-node/usr/lib/
COPY --from=node_toolchain /usr/local/bin/node /opt/anhuan-node/bin/node
COPY --from=node_toolchain /app/src/web/node_modules /app/src/web/node_modules
COPY infra/f1/local-tsc /usr/local/bin/anhuan-local-tsc

RUN set -eu; \
    set -- /opt/anhuan-node/lib/ld-musl-*.so.1; \
    test "$#" -eq 1; \
    ln -s "$1" /opt/anhuan-node/ld-musl.so.1; \
    chmod 0555 /usr/local/bin/anhuan-local-tsc; \
    rm -f /app/src/web/node_modules/.bin/tsc; \
    ln -s /usr/local/bin/anhuan-local-tsc /app/src/web/node_modules/.bin/tsc
