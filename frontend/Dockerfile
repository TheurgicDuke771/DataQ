# DataQ frontend image — build the Vite SPA, serve the static dist via nginx.
# Build context = frontend/ :  docker build -f frontend/Dockerfile frontend/
#
# NOTE: Azure Static Web App serves dist/ directly and does NOT need this image
# (the deploy workflow builds + uploads dist to SWA). This Dockerfile is the
# container option — e.g. running the UI on Container Apps behind the same
# ingress as the API — and keeps the UI deployable without Azure-specific glue.
#
# VITE_* values are inlined at build time (Vite bakes them into the bundle), so
# they're build args, not runtime env. Pass them with --build-arg.
#
# Two published variants share this one Dockerfile (#472):
#   :latest — production build (BUILD_NODE_ENV=production, no dev-bypass). For
#             real-auth self-host: pass your VITE_AZURE_* at build time.
#   :dev    — zero-config eval image: BUILD_NODE_ENV=development (so
#             import.meta.env.DEV is true → the dev-bypass auth gate in
#             src/auth/config.ts activates) + VITE_AUTH_DEV_BYPASS=true. No
#             Azure tenant needed; what docker-compose.ghcr.yml pulls.

FROM node:22-alpine AS build
WORKDIR /app
RUN corepack enable

COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

COPY . .
ARG VITE_AZURE_TENANT_ID=""
ARG VITE_AZURE_SPA_CLIENT_ID=""
ARG VITE_AZURE_API_CLIENT_ID=""
ARG VITE_AZURE_API_SCOPE="user_impersonation"
ARG VITE_AUTH_DEV_BYPASS="false"
# Vite ties import.meta.env.DEV/PROD to NODE_ENV (NOT to --mode), so the eval
# image builds with BUILD_NODE_ENV=development to make DEV true; the default
# production build keeps DEV false (the auth-bypass gate stays inert).
ARG BUILD_NODE_ENV="production"
ENV VITE_AZURE_TENANT_ID=$VITE_AZURE_TENANT_ID \
    VITE_AZURE_SPA_CLIENT_ID=$VITE_AZURE_SPA_CLIENT_ID \
    VITE_AZURE_API_CLIENT_ID=$VITE_AZURE_API_CLIENT_ID \
    VITE_AZURE_API_SCOPE=$VITE_AZURE_API_SCOPE \
    VITE_AUTH_DEV_BYPASS=$VITE_AUTH_DEV_BYPASS
RUN NODE_ENV=$BUILD_NODE_ENV pnpm build

FROM nginx:1.27-alpine AS serve
# Link the GHCR package to this repo (shows under the repo's Packages, lets the
# repo's Actions GITHUB_TOKEN push it, and inherits anonymous-pull visibility —
# ADR 0023, mirrors backend/Dockerfile).
LABEL org.opencontainers.image.source="https://github.com/TheurgicDuke771/DataQ"
LABEL org.opencontainers.image.description="DataQ frontend (React SPA served by nginx)"
LABEL org.opencontainers.image.licenses="MIT"
COPY --from=build /app/dist /usr/share/nginx/html
# Template (not a static conf): the nginx entrypoint envsubst's it at startup so
# the API upstream is configurable. Default targets the compose `api` service.
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
ENV DATAQ_API_UPSTREAM=http://api:8000
EXPOSE 8080
