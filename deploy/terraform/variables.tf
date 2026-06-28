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

# ── Shared Postgres (the app's DB lives on the harness's single server) ───────

variable "shared_pg_server_name" {
  description = "Name of the shared Postgres Flexible Server (harness-owned, neutral name). The app's `dataq` database + `dataq_app` role live here (provisioned out-of-band — see README)."
  type        = string
  default     = "dataq-pg-wus3-3erlgd"
}

variable "app_db_name" {
  description = "The app's database on the shared server (distinct from airflow's)."
  type        = string
  default     = "dataq"
}

variable "app_db_user" {
  description = "Least-privilege role the app connects as (owns only app_db_name)."
  type        = string
  default     = "dataq_app"
}

variable "app_db_password" {
  description = "Password for app_db_user (provisioned out-of-band via psql; pass at apply: TF_VAR_app_db_password=...). Injected as the DATABASE_URL Container App secret; never committed."
  type        = string
  sensitive   = true
}

# ── Backend image (GHCR — ADR 0023) ──────────────────────────────────────────

variable "backend_image_repo" {
  description = "GHCR backend image repository (public package, anonymous ACA pull). Lowercase owner per GHCR."
  type        = string
  default     = "ghcr.io/theurgicduke771/dataq-backend"
}

variable "image_tag" {
  description = "Backend image tag to deploy. Use an IMMUTABLE tag in prod (ACA caches 'latest' at the node, so a same-tag rebuild won't be re-pulled on a new revision). Bump per deploy. (v3 = the #393 App-Insights logging-lock fix; v4 = login page + AZURE_ALLOW_GUEST_USERS support, PR #398.)"
  type        = string
  default     = "v4"
}

variable "azure_allow_guest_users" {
  description = "Allow tenant guest (B2B / external) identities to authenticate (sets AZURE_ALLOW_GUEST_USERS on the API). The app code defaults this off; this deployment enables it because the owner signs in with a guest account."
  type        = bool
  default     = true
}

# ── Sizing ───────────────────────────────────────────────────────────────────

variable "log_retention_days" {
  description = "Log Analytics retention for the Container Apps environment."
  type        = number
  default     = 30
}

# ── Security hardening toggles ───────────────────────────────────────────────

variable "key_vault_purge_protection" {
  description = "Key Vault purge protection. false during bring-up so a destroy/re-apply can reuse the vault name. PROD: set true to make secrets unrecoverable-deletable only after the soft-delete retention window (NOTE: irreversible once enabled)."
  type        = bool
  default     = false
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

# API + SPA client ids are no longer inputs — they're created in sso.tf and wired
# into the app env + outputs directly.

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
