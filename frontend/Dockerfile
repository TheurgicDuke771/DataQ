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
ENV VITE_AZURE_TENANT_ID=$VITE_AZURE_TENANT_ID \
    VITE_AZURE_SPA_CLIENT_ID=$VITE_AZURE_SPA_CLIENT_ID \
    VITE_AZURE_API_CLIENT_ID=$VITE_AZURE_API_CLIENT_ID \
    VITE_AZURE_API_SCOPE=$VITE_AZURE_API_SCOPE \
    VITE_AUTH_DEV_BYPASS=$VITE_AUTH_DEV_BYPASS
RUN pnpm build

FROM nginx:1.27-alpine AS serve
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 8080
