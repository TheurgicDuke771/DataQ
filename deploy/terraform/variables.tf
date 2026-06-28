# Non-secret configuration. Defaults are wired for the single-tenant v1 deploy.
# Override in terraform.tfvars (gitignored) — see terraform.tfvars.example.

variable "project_tag" {
  description = "Tag applied to all resources (matches the harness/RG convention)."
  type        = string
  default     = "dataq"
}

# ── Shared (reused) ──────────────────────────────────────────────────────────

variable "azure_resource_group" {
  description = "Existing RG shared with the harness. Reused, never destroyed."
  type        = string
  default     = "dataq-rg"
}

variable "azure_location" {
  description = "Region for the app resources (Container Apps, KV, SWA, logs)."
  type        = string
  default     = "West US 2"
}

variable "postgres_location" {
  description = "Region for the app Postgres. westus3 because Postgres Flexible Server is offer-restricted in West US 2 for this subscription (same constraint the harness hit). Adjacent to westus2 -> low latency from the Container Apps env."
  type        = string
  default     = "West US 3"
}

# ── Backend image (GHCR — ADR 0023) ──────────────────────────────────────────

variable "backend_image_repo" {
  description = "GHCR backend image repository (public package, anonymous ACA pull). Lowercase owner per GHCR."
  type        = string
  default     = "ghcr.io/theurgicduke771/dataq-backend"
}

variable "image_tag" {
  description = "Backend image tag to deploy. Use an IMMUTABLE tag in prod (ACA caches 'latest' at the node, so a same-tag rebuild won't be re-pulled on a new revision). Bump per deploy."
  type        = string
  default     = "v1"
}

# ── Sizing ───────────────────────────────────────────────────────────────────

variable "postgres_sku" {
  description = "Postgres Flexible Server SKU (Burstable B1ms = cheapest)."
  type        = string
  default     = "B_Standard_B1ms"
}

variable "postgres_admin_login" {
  description = "App Postgres admin login (password is generated, stored in state + KV-adjacent container secret)."
  type        = string
  default     = "dataqadmin"
}

variable "log_retention_days" {
  description = "Log Analytics retention for the Container Apps environment."
  type        = number
  default     = 30
}

variable "swa_sku" {
  description = "Static Web App SKU. Standard is required to link a Container Apps backend (same-origin /api proxy). Drop to Free only with the CORS fallback."
  type        = string
  default     = "Standard"
}

# ── App config (non-secret) ──────────────────────────────────────────────────

variable "environment" {
  description = "ENVIRONMENT value the backend Settings read."
  type        = string
  default     = "prod"
}

variable "workspace_admin_emails" {
  description = "Comma-separated workspace-admin allowlist (WORKSPACE_ADMIN_EMAILS)."
  type        = string
  default     = ""
}

# Azure AD SSO — real auth in prod (AUTH_DEV_BYPASS=false). These are non-secret
# identifiers (MSAL SPA is a public client; there is no SPA secret).
variable "azure_tenant_id" {
  description = "Azure AD tenant id for SSO. Empty = inherit the deployer's tenant."
  type        = string
  default     = ""
}

variable "azure_api_client_id" {
  description = "App-registration (API) client id for token validation."
  type        = string
  default     = ""
}

variable "azure_spa_client_id" {
  description = "App-registration (SPA) client id (public MSAL client)."
  type        = string
  default     = ""
}

variable "azure_api_scope" {
  description = "API scope the SPA requests."
  type        = string
  default     = "user_impersonation"
}

# ── CI deploy (GitHub OIDC) ──────────────────────────────────────────────────

variable "github_repo" {
  description = "owner/repo the Deploy workflow runs from (federated-credential subject)."
  type        = string
  default     = "TheurgicDuke771/DataQ"
}

variable "github_environment" {
  description = "GitHub environment the federated credential is scoped to."
  type        = string
  default     = "production"
}
