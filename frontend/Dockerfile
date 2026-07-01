# DataQ frontend image — build the Vite SPA, serve the static dist via nginx.
# Build context = frontend/ :  docker build -f frontend/Dockerfile frontend/
#
# ONE generic image, nothing baked in (ADR 0028): no cloud, no secret, no
# auth-bypass. Auth config is injected at RUNTIME — nginx serves /config.js from
# the DATAQ_AUTH_* env below (envsubst at startup), and the SPA reads it before
# the bundle runs. So the same image serves the eval stack (DATAQ_AUTH_MODE=bypass)
# and a real deployment (DATAQ_AUTH_MODE=oidc + authority/clientId/scope) — set the
# env at `docker run`, no rebuild. Replaces the #472 two-image (:latest/:dev) split.
#
# The deploy workflow builds + pushes this image and rolls the `dataq-app-frontend`
# Container App (ADR 0028 §5 cutover — the Static Web App is gone; SWA couldn't
# inject runtime config). See deploy/terraform/frontend.tf.

FROM node:22-alpine AS build
WORKDIR /app
RUN corepack enable

COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

COPY . .
RUN pnpm build

FROM nginx:1.27-alpine AS serve
# Link the GHCR package to this repo (shows under the repo's Packages, lets the
# repo's Actions GITHUB_TOKEN push it, and inherits anonymous-pull visibility —
# ADR 0023, mirrors backend/Dockerfile).
LABEL org.opencontainers.image.source="https://github.com/TheurgicDuke771/DataQ"
LABEL org.opencontainers.image.description="DataQ frontend (React SPA served by nginx)"
LABEL org.opencontainers.image.licenses="MIT"
COPY --from=build /app/dist /usr/share/nginx/html
# Template (not a static conf): the nginx entrypoint envsubst's it at startup so
# the API upstream + auth config are runtime-configurable.
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
# Opt into the nginx image's built-in resolver detection: its
# 15-local-resolvers.envsh only exports ${NGINX_LOCAL_RESOLVERS} (the
# /etc/resolv.conf nameservers, used by the proxy `resolver` directive) when this
# flag is set. Cloud-neutral (ADR 0028) — no resolver IP baked in; works in
# compose (127.0.0.11) and ACA/K8s (cluster DNS) alike.
ENV NGINX_ENTRYPOINT_LOCAL_RESOLVERS=1
# Runtime config env (ADR 0028) — MUST be defined (even if empty) so envsubst
# always substitutes them in /config.js; empty → SPA is unconfigured (fail-closed),
# bypass needs an explicit DATAQ_AUTH_MODE=bypass. DATAQ_API_UPSTREAM defaults to
# the compose `api` service.
ENV DATAQ_API_UPSTREAM=http://api:8000 \
    DATAQ_AUTH_MODE="" \
    DATAQ_AUTH_AUTHORITY="" \
    DATAQ_AUTH_CLIENT_ID="" \
    DATAQ_AUTH_API_SCOPE=""
EXPOSE 8080
