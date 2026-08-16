# SES — the email alert channel (#1368), the AWS-native counterpart of the
# SMTP creds the Azure stack keeps in Key Vault. Everything here is gated on
# var.alert_email: empty (the default) creates nothing and the app's email
# channel stays off (config.py enables it only when EMAIL_TO + EMAIL_USERNAME
# + EMAIL_PASSWORD_SECRET_NAME are all set).
#
# Sandbox posture, stated honestly: a fresh SES account can only send FROM and
# TO verified identities, so alert_email is both the sender and the sole
# recipient — fine for a single-operator deployment; production fan-out needs
# an SES production-access request (an AWS support form, not Terraform).
#
# The identity requires a ONE-TIME human step: SES emails a verification link
# to alert_email at create time, and sends fail with "Email address is not
# verified" until it is clicked. Check with:
#   aws ses get-identity-verification-attributes --identities <alert_email>

locals {
  # One gate for every resource in this file (PR #1370 review — the condition
  # was copy-pasted per resource, so a future edit could invert one silently).
  ses_enabled = var.alert_email == "" ? 0 : 1
  # Recipient defaults to the sender; a distinct alert_email_to needs its OWN
  # verified identity while the account is in the SES sandbox.
  alert_email_to = var.alert_email_to != "" ? var.alert_email_to : var.alert_email
}

resource "aws_ses_email_identity" "alert" {
  count = local.ses_enabled
  email = var.alert_email
}

# Sandbox rule: the RECIPIENT must be verified too, so a distinct TO address
# gets its own identity (its own one-time verification click). Collapses to
# nothing when alert_email_to is unset/equal to the sender.
resource "aws_ses_email_identity" "alert_to" {
  count = local.ses_enabled == 1 && local.alert_email_to != var.alert_email ? 1 : 0
  email = local.alert_email_to
}

# Dedicated IAM user for SMTP: SES SMTP credentials ARE an IAM access key —
# the username is the key id and the password is Terraform's derived
# ses_smtp_password_v4 (the documented SigV4 derivation, region-specific).
# Scoped to SendRawEmail only (what SMTP submission uses), from this identity.
resource "aws_iam_user" "ses_smtp" {
  count = local.ses_enabled
  name  = "dataq-app-ses-smtp"
  tags  = { Name = "dataq-app-ses-smtp" }
}

data "aws_iam_policy_document" "ses_smtp" {
  count = local.ses_enabled
  statement {
    actions   = ["ses:SendRawEmail"]
    resources = [aws_ses_email_identity.alert[0].arn]
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

# The SMTP password goes into Secrets Manager UNDER THE APP PREFIX
# (dataq/channel-email-password): the task env's EMAIL_PASSWORD_SECRET_NAME
# already names `channel-email-password`, which the app resolves through its
# own SecretStore (SECRET_STORE=aws_secrets_manager + AWS_SECRETS_MANAGER_PREFIX
# = dataq/) at send time — same read path as every connection credential, no
# extra task-definition secret injection.
resource "aws_secretsmanager_secret" "channel_email_password" {
  count = local.ses_enabled
  name  = "${var.aws_secrets_manager_prefix}/channel-email-password"
  # The app's sweep/rotation owns secrets under dataq/, but this one is
  # infra-owned: recovery_window 0 so a destroy/recreate cycle doesn't collide
  # with a soft-deleted name.
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
