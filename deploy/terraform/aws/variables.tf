# Non-secret configuration.

variable "aws_region" {
  description = "AWS region for every resource in this stack."
  type        = string
  default     = "us-east-2"
}

# ── Database ──────────────────────────────────────────────────────────────

variable "app_db_name" {
  description = "The app's database name."
  type        = string
  default     = "dataq"
}

variable "app_db_user" {
  description = "Least-privilege role the app connects as."
  type        = string
  default     = "dataq_app"
}

variable "app_db_password" {
  description = "Password for app_db_user. Pass at apply: TF_VAR_app_db_password=... . Injected into Secrets Manager as an infra-owned secret; never committed."
  type        = string
  sensitive   = true
}

# ── Images (GHCR — ADR 0023, cloud-agnostic) ────────────────────────────────

variable "backend_image_repo" {
  description = "GHCR backend image repository (public package, anonymous pull)."
  type        = string
  default     = "ghcr.io/theurgicduke771/dataq-backend"
}

variable "frontend_image_repo" {
  description = "GHCR frontend (nginx SPA) image repository — one generic runtime-configured image (ADR 0028)."
  type        = string
  default     = "ghcr.io/theurgicduke771/dataq-frontend"
}

variable "image_tag" {
  description = <<-DESC
    Backend image tag the task definitions are CREATED with (immutable tag;
    later rollouts happen out-of-band via CI, ignore_changes on the container
    image). No default on purpose (#1349): the services genuinely RUN this
    image from the first apply, and a stale hardcoded default (the old "v10",
    a June image predating the generic-OIDC auth and the aws_secrets_manager
    store) crash-loops from minute one. publish-images.yml tags main merges
    as `main-<full sha>` — the bare `<sha>` tag belongs to the manual Azure
    deploy.yml and usually does not exist — and skips docs-only merges, so
    pass `main-<sha of the last image-bearing main commit>` and verify the
    tag exists first (README.md, Apply).
  DESC
  type        = string
}

variable "frontend_image_tag" {
  description = "Frontend image tag the task definition is created with. Same no-default reasoning, tag format (main-<sha>) + out-of-band rollout note as image_tag (publish-images.yml pushes backend + frontend under the SAME tag set)."
  type        = string
}

# ── App config (non-secret) ──────────────────────────────────────────────

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

# ── WAF (#1388) ─────────────────────────────────────────────────────────────
variable "waf_enabled" {
  description = "Attach a WAFv2 Web ACL to the CloudFront distribution. Costs roughly $7/month plus request charges; set false to remove it entirely."
  type        = bool
  default     = true
}

variable "waf_rate_limit_per_5min" {
  description = "WAF per-IP request ceiling over a trailing 5-minute window. Deliberately well above the app's own 120/min unauthenticated class - this catches floods, it does not shape normal traffic."
  type        = number
  default     = 2000

  validation {
    # WAFv2's own floor for a rate-based statement. Below it the apply fails with
    # a provider-level error that does not name the limit.
    condition     = var.waf_rate_limit_per_5min >= 100
    error_message = "WAF rate-based statements require a limit of at least 100."
  }
}

variable "waf_max_body_bytes" {
  description = "Largest request body accepted at the edge. DataQ's write surface is small JSON; anything much larger is a probe. Must stay under 16384 - see the validation."
  type        = number
  default     = 8192

  validation {
    # The upper bound is the load-bearing half.
    condition     = var.waf_max_body_bytes > 0 && var.waf_max_body_bytes < 16384
    error_message = "waf_max_body_bytes must be between 1 and 16383: WAF inspects at most 16KB of a CloudFront body, so anything larger makes the rule unmatchable."
  }
}

# ── OIDC access gate (#1386) ──────────────────────────────────────────────── The second layer
# behind `allow_admin_create_user_only` in cognito.tf.
variable "oidc_allowed_emails" {
  description = "Comma-separated addresses allowed to hold a DataQ account via OIDC (OIDC_ALLOWED_EMAILS). Empty = no app-side gate."
  type        = string
  default     = ""
}

variable "oidc_allowed_domains" {
  description = "Comma-separated email domains allowed to hold a DataQ account via OIDC (OIDC_ALLOWED_DOMAINS). Empty = no app-side gate."
  type        = string
  default     = ""
}

# Replaces the earlier email_username/email_from/email_to trio (#1368): the stack now ships SES
# natively (ses.tf), so one address drives the whole channel — it becomes the SES identity, the
# From:, and (sandbox) the sole recipient; the SMTP login + password are derived resources, not
# inputs.
variable "alert_email" {
  description = "Verified SES identity for email alerts: the sender (From:), and the default recipient when alert_email_to is unset. Empty = email channel off. Requires the one-time SES verification click."
  type        = string
  default     = ""
}

variable "alert_email_to" {
  description = "Recipient(s) for email alerts (EMAIL_TO) when different from alert_email. In the SES sandbox this address needs its own verification click (a second identity is created for it)."
  type        = string
  default     = ""
}

# ── Secrets (AWS Secrets Manager — SECRET_STORE=aws_secrets_manager) ───────

variable "aws_secrets_manager_prefix" {
  description = "Namespace prefix for datasource-credential secrets the app manages at runtime (AWS_SECRETS_MANAGER_PREFIX). Distinct from the infra-owned bootstrap secrets (DB URL etc.), which live under a separate dataq-app-infra/ prefix this stack owns directly."
  type        = string
  default     = "dataq"
}

# ── CI deploy (GitHub OIDC) ─────────────────────────────────────────────────

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

# ── State encryption (OpenTofu) ─────────────────────────────────────────────

variable "state_encryption_passphrase" {
  description = <<-DESC
    Passphrase for OpenTofu state encryption (versions.tf `encryption` block).

    Supplied from the gitignored terraform.tfvars. This is a DATA-AT-REST KEY,
    not a credential: it cannot be revoked and it cannot be re-minted. Losing
    it means terraform.tfstate is unrecoverable — keep a second copy off this
    machine. See README.md.
  DESC
  type        = string
  sensitive   = true
}

# ── Sizing / retention ───────────────────────────────────────────────────

variable "log_retention_days" {
  description = "CloudWatch Logs retention for every DataQ log group."
  type        = number
  default     = 30
}
