{{- define "dataq.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "dataq.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "dataq.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
app.kubernetes.io/name: {{ include "dataq.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{/* Selector labels for one component: (dict "ctx" $ "component" "api") */}}
{{- define "dataq.selectorLabels" -}}
app.kubernetes.io/name: {{ include "dataq.name" .ctx }}
app.kubernetes.io/instance: {{ .ctx.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{- define "dataq.componentLabels" -}}
{{ include "dataq.labels" .ctx }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{- define "dataq.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "dataq.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{/* (dict "ctx" $ "image" .Values.image.backend) */}}
{{- define "dataq.image" -}}
{{- $tag := default .ctx.Chart.AppVersion .image.tag -}}
{{- if .image.digest -}}
{{- printf "%s/%s:%s@%s" .ctx.Values.image.registry .image.repository $tag .image.digest -}}
{{- else -}}
{{- printf "%s/%s:%s" .ctx.Values.image.registry .image.repository $tag -}}
{{- end -}}
{{- end -}}

{{- define "dataq.apiScheme" -}}
{{- if .Values.api.tls.existingSecret -}}https{{- else -}}http{{- end -}}
{{- end -}}

{{- define "dataq.publicBaseUrl" -}}
{{- if .Values.config.publicBaseUrl -}}
{{- .Values.config.publicBaseUrl -}}
{{- else if and .Values.ingress.enabled .Values.ingress.hosts -}}
{{- $host := (index .Values.ingress.hosts 0).host -}}
{{- range .Values.ingress.tls -}}
{{- if has $host (default list .hosts) -}}
{{- printf "https://%s" $host -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
Refuses a configuration that would boot unsafe or not at all. Called from every manifest that
depends on it, so `helm template` fails as well as `helm install`.
*/}}
{{- define "dataq.validate" -}}
{{- if not .Values.existingSecret -}}
{{- fail "existingSecret is required: create a Secret holding DATABASE_URL, REDIS_URL and the secret-store credential, and name it here" -}}
{{- end -}}
{{- $mode := .Values.auth.mode -}}
{{- if eq $mode "bypass" -}}
{{- if not .Values.auth.allowDevBypass -}}
{{- fail "auth.mode=bypass signs everyone in as an admin with no credential; set auth.allowDevBypass=true to confirm this is a throwaway install" -}}
{{- end -}}
{{- if ne .Values.config.environment "dev" -}}
{{- fail "auth.mode=bypass needs config.environment=dev; the api refuses the bypass anywhere else" -}}
{{- end -}}
{{- else if eq $mode "otp" -}}
{{- $o := .Values.auth.otp -}}
{{- if not (and $o.smtpHost $o.username $o.from $o.passwordSecretName) -}}
{{- fail "auth.mode=otp needs auth.otp.smtpHost, username, from and passwordSecretName" -}}
{{- end -}}
{{- if not (or $o.allowedEmails $o.allowedDomains) -}}
{{- fail "auth.mode=otp needs auth.otp.allowedEmails or allowedDomains: there is no open registration" -}}
{{- end -}}
{{- else if eq $mode "oidc" -}}
{{- $o := .Values.auth.oidc -}}
{{- if not (and $o.issuer $o.audience $o.clientId) -}}
{{- fail "auth.mode=oidc needs auth.oidc.issuer, audience and clientId" -}}
{{- end -}}
{{- if not (or $o.allowedEmails $o.allowedDomains $o.openSignup) -}}
{{- fail "auth.mode=oidc needs auth.oidc.allowedEmails or allowedDomains; without one, anyone your identity provider lets register gets in. Set auth.oidc.openSignup=true if the provider itself is the gate" -}}
{{- end -}}
{{- end -}}
{{- $store := .Values.secretStore.backend -}}
{{- if and (eq $store "openbao") (not .Values.secretStore.openbao.addr) -}}
{{- fail "secretStore.backend=openbao needs secretStore.openbao.addr" -}}
{{- end -}}
{{- if and (eq $store "azure_key_vault") (not .Values.secretStore.azureKeyVault.url) -}}
{{- fail "secretStore.backend=azure_key_vault needs secretStore.azureKeyVault.url" -}}
{{- end -}}
{{- $tel := .Values.config.telemetry -}}
{{- if and (eq $tel.sink "file") (not $tel.existingClaim) -}}
{{- fail "config.telemetry.sink=file needs config.telemetry.existingClaim: the root filesystem is read-only, so the events file needs a volume" -}}
{{- end -}}
{{- $tag := default .Chart.AppVersion .Values.image.backend.tag -}}
{{- if and (ne $tel.sink "off") (regexMatch "^v?[0-9]+\\.[0-9]+\\.[0-9]+$" $tag) (semverCompare "<1.3.0-0" $tag) -}}
{{- fail (printf "config.telemetry.sink=%s needs a backend image from v1.3.0 on; %s ignores the setting and records nothing" $tel.sink $tag) -}}
{{- end -}}
{{- $managed := include "dataq.chartEnv" . | fromYaml -}}
{{- range $name, $_ := .Values.config.extraEnv -}}
{{- $upper := upper $name -}}
{{- if and (regexMatch "PASSWORD|TOKEN|(^|_)SECRET(_ID)?$|^KV_SECRET_|_KEY$|CONNECTION_STRING|^DATABASE_URL$|^REDIS_URL$" $upper) (not (hasSuffix "_NAME" $upper)) -}}
{{- fail (printf "config.extraEnv.%s looks like a credential; put it in the existingSecret instead" $name) -}}
{{- end -}}
{{- if or (hasKey $managed $upper) (has $upper (list "ENVIRONMENT" "AUTH_DEV_BYPASS" "SECRET_STORE" "TELEMETRY_SINK" "TELEMETRY_FILE_PATH")) (regexMatch "^(AUTH_EMAIL_|AUTH_OTP_|AUTH_OIDC_|OIDC_|OPENBAO_)" $upper) -}}
{{- fail (printf "config.extraEnv.%s is set by the chart from its own values; use those instead" $name) -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/* The settings the chart derives from its own values, NAME: value. */}}
{{- define "dataq.chartEnv" -}}
{{- $v := .Values -}}
{{- $env := dict
  "ENVIRONMENT" $v.config.environment
  "LOG_LEVEL" $v.config.logLevel
  "SECRET_STORE" $v.secretStore.backend
  "AUTH_DEV_BYPASS" (ternary "true" "false" (eq $v.auth.mode "bypass"))
  "WORKSPACE_ADMIN_EMAILS" $v.auth.workspaceAdminEmails
  "CORS_ALLOW_ORIGINS" ""
  "PUBLIC_BASE_URL" (include "dataq.publicBaseUrl" .)
  "RATE_LIMIT_ENABLED" (ternary "true" "false" $v.config.rateLimitEnabled)
  "RATE_LIMIT_XFF_TRUSTED_HOPS" (toString (default (ternary 2 1 $v.ingress.enabled) $v.config.rateLimitXffTrustedHops))
  "WAREHOUSE_LINEAGE_ENABLED" (ternary "true" "false" $v.config.warehouseLineageEnabled)
-}}
{{- with $v.config.deploymentRegion }}{{ $_ := set $env "DEPLOYMENT_REGION" . }}{{ end -}}
{{- with $v.config.otlpEndpoint }}{{ $_ := set $env "OTEL_EXPORTER_OTLP_ENDPOINT" . }}{{ end -}}
{{- if ne $v.config.telemetry.sink "off" }}{{ $_ := set $env "TELEMETRY_SINK" $v.config.telemetry.sink }}{{ end -}}
{{- if eq $v.config.telemetry.sink "file" }}{{ $_ := set $env "TELEMETRY_FILE_PATH" "/var/lib/dataq/usage-events.jsonl" }}{{ end -}}
{{- if eq $v.secretStore.backend "openbao" -}}
{{- $_ := set $env "OPENBAO_ADDR" $v.secretStore.openbao.addr -}}
{{- $_ := set $env "OPENBAO_MOUNT" $v.secretStore.openbao.mount -}}
{{- with $v.secretStore.openbao.caBundle }}{{ $_ := set $env "OPENBAO_CA_BUNDLE" . }}{{ end -}}
{{- else if eq $v.secretStore.backend "azure_key_vault" -}}
{{- $_ := set $env "AZURE_KEY_VAULT_URL" $v.secretStore.azureKeyVault.url -}}
{{- with $v.secretStore.azureKeyVault.clientId }}{{ $_ := set $env "AZURE_CLIENT_ID" . }}{{ end -}}
{{- else if eq $v.secretStore.backend "aws_secrets_manager" -}}
{{- $_ := set $env "AWS_SECRETS_MANAGER_PREFIX" $v.secretStore.awsSecretsManager.prefix -}}
{{- end -}}
{{- if eq $v.auth.mode "otp" -}}
{{- $o := $v.auth.otp -}}
{{- $_ := set $env "AUTH_EMAIL_SMTP_HOST" $o.smtpHost -}}
{{- $_ := set $env "AUTH_EMAIL_SMTP_PORT" (toString $o.smtpPort) -}}
{{- $_ := set $env "AUTH_EMAIL_TLS_MODE" $o.tlsMode -}}
{{- $_ := set $env "AUTH_EMAIL_USERNAME" $o.username -}}
{{- $_ := set $env "AUTH_EMAIL_FROM" $o.from -}}
{{- $_ := set $env "AUTH_EMAIL_PASSWORD_SECRET_NAME" $o.passwordSecretName -}}
{{- with $o.caBundle }}{{ $_ := set $env "AUTH_EMAIL_CA_BUNDLE" . }}{{ end -}}
{{- $_ := set $env "AUTH_OTP_ALLOWED_EMAILS" $o.allowedEmails -}}
{{- $_ := set $env "AUTH_OTP_ALLOWED_DOMAINS" $o.allowedDomains -}}
{{- $_ := set $env "AUTH_OTP_DEFAULT_ROLE" $o.defaultRole -}}
{{- else if eq $v.auth.mode "oidc" -}}
{{- $o := $v.auth.oidc -}}
{{- $_ := set $env "OIDC_ISSUER" $o.issuer -}}
{{- $_ := set $env "OIDC_AUDIENCE" $o.audience -}}
{{- $_ := set $env "OIDC_ALLOWED_EMAILS" $o.allowedEmails -}}
{{- $_ := set $env "OIDC_ALLOWED_DOMAINS" $o.allowedDomains -}}
{{- $_ := set $env "AUTH_OIDC_DEFAULT_ROLE" $o.defaultRole -}}
{{- end -}}
{{- toYaml $env -}}
{{- end -}}

{{/* Non-secret backend environment, NAME: value. One source for the ConfigMap and the migrate hook. */}}
{{- define "dataq.backendEnv" -}}
{{- $env := include "dataq.chartEnv" . | fromYaml -}}
{{- range $k, $val := .Values.config.extraEnv -}}
{{- $_ := set $env $k (toString $val) -}}
{{- end -}}
{{- toYaml $env -}}
{{- end -}}

{{/* Listed before the chart's ConfigMap: a later source wins, so the chart's settings beat a same-named Secret key. */}}
{{- define "dataq.backendEnvFrom" -}}
- secretRef:
    name: {{ .Values.existingSecret }}
{{- range .Values.extraEnvFromSecrets }}
- secretRef:
    name: {{ . }}
{{- end }}
{{- end -}}

{{/* (dict "ctx" $ "telemetry" true) — the migrate hook passes false: it writes no events. */}}
{{- define "dataq.backendVolumes" -}}
{{- $v := .ctx.Values -}}
- name: tmp
  emptyDir: {}
- name: home
  emptyDir: {}
{{- with $v.trustedCA.existingConfigMap }}
- name: trusted-ca
  configMap:
    name: {{ . }}
{{- end }}
{{- if and .telemetry (eq $v.config.telemetry.sink "file") }}
- name: telemetry
  persistentVolumeClaim:
    claimName: {{ $v.config.telemetry.existingClaim }}
{{- end }}
{{- end -}}

{{- define "dataq.backendVolumeMounts" -}}
{{- $v := .ctx.Values -}}
- name: tmp
  mountPath: /tmp
- name: home
  mountPath: /home/dataq
{{- if $v.trustedCA.existingConfigMap }}
- name: trusted-ca
  mountPath: /etc/dataq/ca
  readOnly: true
{{- end }}
{{- if and .telemetry (eq $v.config.telemetry.sink "file") }}
- name: telemetry
  mountPath: /var/lib/dataq
{{- end }}
{{- end -}}

{{/* Pod-level fields shared by every backend workload: (dict "ctx" $ "component" "api") */}}
{{- define "dataq.backendPodSpec" -}}
{{- $v := .ctx.Values -}}
serviceAccountName: {{ include "dataq.serviceAccountName" .ctx }}
automountServiceAccountToken: false
# Service-link variables (<SERVICE>_PORT=tcp://...) would land on settings of the same name.
enableServiceLinks: false
{{- with $v.imagePullSecrets }}
imagePullSecrets:
  {{- toYaml . | nindent 2 }}
{{- end }}
securityContext:
  {{- toYaml $v.backendSecurityContext.pod | nindent 2 }}
{{- with $v.nodeSelector }}
nodeSelector:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with $v.tolerations }}
tolerations:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with $v.affinity }}
affinity:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- end -}}

{{- define "dataq.podMetadata" -}}
labels:
  {{- include "dataq.selectorLabels" . | nindent 2 }}
  {{- with .ctx.Values.podLabels }}
  {{- toYaml . | nindent 2 }}
  {{- end }}
annotations:
  checksum/config: {{ include "dataq.backendEnv" .ctx | sha256sum }}
  {{- with .ctx.Values.podAnnotations }}
  {{- toYaml . | nindent 2 }}
  {{- end }}
{{- end -}}

{{/* A probe from values; httpGet switches to HTTPS when the api serves TLS: (dict "probe" . "tls" $tls) */}}
{{- define "dataq.probe" -}}
{{- $p := deepCopy .probe -}}
{{- if and .tls $p.httpGet -}}
{{- $_ := set $p.httpGet "scheme" "HTTPS" -}}
{{- end -}}
{{- toYaml $p -}}
{{- end -}}
