# SES — the email alert channel (#1368), the AWS-native counterpart of the SMTP creds the Azure
# stack keeps in Key Vault.

locals {
  # One gate for every resource in this file (PR #1370 review — the condition
  # was copy-pasted per resource, so a future edit could invert one silently).
  ses_enabled = var.alert_email == "" ? 0 : 1
  # Recipients default to the sender; every distinct recipient needs its OWN verified identity while
  # the account is in the SES sandbox.
  alert_email_to = var.alert_email_to != "" ? var.alert_email_to : var.alert_email
  alert_to_identities = local.ses_enabled == 1 ? toset([
    for a in split(",", local.alert_email_to) : trimspace(a)
    if trimspace(a) != "" && trimspace(a) != var.alert_email
  ]) : toset([])
}

# The channel only turns on when the SENDER exists (config.py gates on EMAIL_USERNAME, which derives
# from alert_email) — setting only the recipient is a silent no-op.
check "alert_email_to_requires_sender" {
  assert {
    condition     = var.alert_email_to == "" || var.alert_email != ""
    error_message = "alert_email_to is set but alert_email is empty - the email channel stays OFF (no sender identity/SMTP credential is created and EMAIL_USERNAME stays empty). Set alert_email too."
  }
}

resource "aws_ses_email_identity" "alert" {
  count = local.ses_enabled
  email = var.alert_email
}

# Sandbox rule: every RECIPIENT must be verified too, so each distinct TO address gets its own
# identity (its own one-time verification click).
resource "aws_ses_email_identity" "alert_to" {
  for_each = local.alert_to_identities
  email    = each.value
}

# Dedicated IAM user for SMTP: SES SMTP credentials ARE an IAM access key — the username is the key
# id and the password is Terraform's derived ses_smtp_password_v4 (the documented SigV4 derivation,
# region-specific).
resource "aws_iam_user" "ses_smtp" {
  count = local.ses_enabled
  name  = "dataq-app-ses-smtp"
  tags  = { Name = "dataq-app-ses-smtp" }
}

data "aws_iam_policy_document" "ses_smtp" {
  count = local.ses_enabled
  statement {
    actions = ["ses:SendRawEmail"]
    # Sender AND every recipient identity (#1373): SES authorizes the send against the RECIPIENT
    # identity ARNs too while the account is in the sandbox.
    resources = concat(
      [aws_ses_email_identity.alert[0].arn],
      [for identity in aws_ses_email_identity.alert_to : identity.arn],
    )
  }
}

resource "aws_iam_user_policy" "ses_smtp" {
  count  = local.ses_enabled
  name   = "dataq-app-ses-smtp-send"
  user   = aws_iam_user.ses_smtp[0].name
  policy = data.aws_iam_policy_document.ses_smtp[0].json
}

resource "aws_iam_access_key" "ses_smtp" {
  count = local.ses_enabled
  user  = aws_iam_user.ses_smtp[0].name
}

# The SMTP password goes into Secrets Manager UNDER THE APP PREFIX (dataq/channel-email-password):
# the task env's EMAIL_PASSWORD_SECRET_NAME already names `channel-email-password`.
resource "aws_secretsmanager_secret" "channel_email_password" {
  count = local.ses_enabled
  name  = "${var.aws_secrets_manager_prefix}/channel-email-password"
  # The app's sweep/rotation owns secrets under dataq/, but this one is infra-owned: recovery_window
  # 0 so a destroy/recreate cycle doesn't collide with a soft-deleted name.
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "channel_email_password" {
  count         = local.ses_enabled
  secret_id     = aws_secretsmanager_secret.channel_email_password[0].id
  secret_string = aws_iam_access_key.ses_smtp[0].ses_smtp_password_v4
}

locals {
  # Consumed by ecs.tf's app env. Region-matched SMTP endpoint; STARTTLS :587
  # (the app's email_smtp_port default).
  ses_smtp_host  = "email-smtp.${var.aws_region}.amazonaws.com"
  email_username = var.alert_email == "" ? "" : aws_iam_access_key.ses_smtp[0].id
}
