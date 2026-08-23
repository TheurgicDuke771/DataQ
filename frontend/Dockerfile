# DataQ frontend image — build the Vite SPA, serve the static dist via nginx.

# Pin the build stage to the native BUILD platform (amd64 on CI runners) even for an arm64 target
# image: a Vite SPA compiles to architecture-independent static assets.
FROM --platform=$BUILDPLATFORM node:22-alpine AS build
WORKDIR /app
RUN corepack enable

COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

COPY . .
RUN pnpm build

FROM nginx:1.27-alpine AS serve
# Link the GHCR package to this repo (shows under the repo's Packages, lets the repo's Actions
# GITHUB_TOKEN push it, and inherits anonymous-pull visibility — ADR 0023, mirrors
LABEL org.opencontainers.image.source="https://github.com/TheurgicDuke771/DataQ"
LABEL org.opencontainers.image.description="DataQ frontend (React SPA served by nginx)"
LABEL org.opencontainers.image.licenses="MIT"
COPY --from=build /app/dist /usr/share/nginx/html
# Template (not a static conf): the nginx entrypoint envsubst's it at startup so
# the API upstream + auth config are runtime-configurable.
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
# Security headers (#1387), `include`d from the template above.
COPY nginx-security-headers.conf /etc/nginx/nginx-security-headers.conf
# Opt into the nginx image's built-in resolver detection: its 15-local-resolvers.envsh only exports
# ${NGINX_LOCAL_RESOLVERS} (the /etc/resolv.conf nameservers.
ENV NGINX_ENTRYPOINT_LOCAL_RESOLVERS=1
# Runtime config env (ADR 0028) — MUST be defined (even if empty) so envsubst always substitutes
# them in /config.js; empty → SPA is unconfigured (fail-closed).
ENV DATAQ_API_UPSTREAM=http://api:8000 \
    DATAQ_AUTH_MODE="" \
    DATAQ_AUTH_AUTHORITY="" \
    DATAQ_AUTH_CLIENT_ID="" \
    DATAQ_AUTH_API_SCOPE="" \
    DATAQ_AUTH_SCOPE="" \
    DATAQ_AUTH_LOGOUT_STYLE="" \
    DATAQ_ORIGIN_SECRET="" \
    DATAQ_CSP_CONNECT_SRC="https:"
EXPOSE 8080
