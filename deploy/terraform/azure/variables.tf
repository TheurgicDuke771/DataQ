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
  description = "Region for the app resources (Container Apps, KV, logs)."
  type        = string
  default     = "West US 2"
}

# The region the shared PostgreSQL server is EXPECTED to be in (G4/#434).
#
# Null means "wherever azure_location says" — right for any ordinary deployment,
# and it keeps the `check` in postgres.tf silent. Set it only where the app is
# attached to a server someone else created, in a region this stack does not
# choose; that makes the mismatch a recorded decision instead of unexplained drift.
#
# A variable rather than prose, and null rather than a baked-in region, because
# both alternatives break the detector: comparing against `azure_location`
# unconditionally would warn on every plan of an exception-carrying deployment, and
# a hard-coded default would hand that same permanently-firing warning to everyone
# else. A check people learn to skip masks the drift that matters — the server
# moving to another *jurisdiction*, which is the unit GDPR Ch. V keys on.
#
# The value itself belongs in a deployment's own tfvars, not in this repo.
variable "shared_pg_expected_location" {
  description = "Region the shared PostgreSQL server is expected in. Null = same as azure_location; set it only where an accepted exception applies (see #1465)."
  type        = string
  default     = null
}


# ── Shared Postgres (the app's DB lives on the harness's single server) ───────

# No default, deliberately. This names a PRE-EXISTING server that this stack does
# not create, so there is no value that is correct for more than one deployment —
# and a committed default would publish the maintainers' server name in a public
# repo. Set it in your own (gitignored) tfvars; Terraform fails clearly if unset.
variable "shared_pg_server_name" {
  description = "Name of the pre-existing Postgres Flexible Server hosting the app's database. The app's database + least-privilege role live here (provisioned out-of-band — see README)."
  type        = string
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

variable "frontend_image_repo" {
  description = "GHCR frontend (nginx SPA) image repository — one generic runtime-configured image (ADR 0028). Public package, anonymous ACA pull."
  type        = string
  default     = "ghcr.io/theurgicduke771/dataq-frontend"
}

variable "frontend_image_tag" {
  description = "Frontend image tag to deploy. Use an IMMUTABLE tag in prod (ACA caches 'latest' at the node). Bump per deploy. (v1 = the ADR 0028 §5 SWA→Container-App cutover; v2 = nginx proxy_http_version 1.1 so ACA ingress stops 426ing the /api + /healthz proxy.) The live image is rolled out-of-band (ignore_changes on the container image), so this is the create-time default."
  type        = string
  default     = "v2"
}

variable "image_tag" {
  description = "Backend image tag to deploy. Use an IMMUTABLE tag in prod (ACA caches 'latest' at the node, so a same-tag rebuild won't be re-pulled on a new revision). Bump per deploy. (v3 = the #393 App-Insights logging-lock fix; v4 = login page + AZURE_ALLOW_GUEST_USERS support, PR #398; v5 = (superseded) ; v7 = #405 beat-lock + #406 KV AZURE_CLIENT_ID; v8 = Slack+email alerting #413; v9 = column-aware redaction #417 + the #383/#384/#395/#423 hardening batch; v10 = freshness/volume monitors #426 + authoring UI #437 + runs-table outcome #425 — the live prod image.)"
  type        = string
  default     = "v10"
}

variable "azure_allow_guest_users" {
  description = "Allow tenant guest (B2B / external) identities to authenticate (sets AZURE_ALLOW_GUEST_USERS on the API). SECURE DEFAULT off — matches the app code default and keeps the BYOL/customer baseline (ADR 0013) locked down. Opt in explicitly per deployment (this one sets it true via tfvars/TF_VAR because the owner signs in with a guest account)."
  type        = bool
  default     = false
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

# Email alerting addresses (PII → set in the gitignored tfvars, not committed).
# Empty = email channel off (the publisher self-no-ops). The Gmail app-password
# lives in Key Vault as `channel-email-password`; these are just the addresses.
variable "email_username" {
  description = "SMTP login / sender for the email alert channel (e.g. a Gmail address). Empty = off."
  type        = string
  default     = ""
}

variable "email_from" {
  description = "From: address for email alerts (defaults to email_username when empty)."
  type        = string
  default     = ""
}

variable "email_to" {
  description = "Comma-separated recipients for email alerts. Empty = email channel off."
  type        = string
  default     = ""
}

# Azure AD SSO — real auth in prod (AUTH_DEV_BYPASS=false). These are non-secret
# identifiers (the OIDC SPA is a public client; there is no SPA secret).
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

# ── State encryption (OpenTofu, #1087) ───────────────────────────────────────
variable "state_encryption_passphrase" {
  description = <<-DESC
    Passphrase for OpenTofu state encryption (versions.tf `encryption` block).

    Supplied from the gitignored terraform.tfvars — deliberately NOT .env, because
    scripts/setup.sh regenerates .env and regenerating this value would make the
    state permanently unreadable.

    This is a DATA-AT-REST KEY, not a credential: it cannot be revoked and it cannot
    be re-minted. Losing it means terraform.tfstate is unrecoverable, which is worse
    than the plaintext exposure encryption removes — so a second copy must live off
    this machine. See deploy/README.md.
  DESC
  type        = string
  sensitive   = true
}
