{{/*
Refuse to render with placeholder secrets, pinned-only image tags and
production-incompatible host / origin settings.

Call at the top of every workload template AND every template that reads
secrets from .Values.secrets.* or backend.env.*:
  {{- include "smsly.validateValues" . }}

Production is detected by comparing global.environment to "production"
(case-sensitive). Set `global.environment: "production"` (lower-case) to
opt in to the production-only checks.
*/}}
{{- define "smsly.validateValues" -}}
{{- $forbidden := list "change-me" "change-me-in-prod" "latest" "" -}}
{{- $prod := eq (default "production" .Values.global.environment) "production" -}}

{{- /* secrets.* placeholders */ -}}
{{- if has .Values.secrets.secretKey $forbidden -}}
{{- fail (printf "smsly.security: secrets.secretKey must be set to a strong random value (got %q). Generate with: python -c \"from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())\"" .Values.secrets.secretKey) -}}
{{- end -}}
{{- if has .Values.secrets.dbPassword $forbidden -}}
{{- fail (printf "smsly.security: secrets.dbPassword must be set (got %q)" .Values.secrets.dbPassword) -}}
{{- end -}}
{{- if has .Values.secrets.redisPassword $forbidden -}}
{{- fail (printf "smsly.security: secrets.redisPassword must be set (got %q)" .Values.secrets.redisPassword) -}}
{{- end -}}
{{- if has .Values.secrets.fieldEncryptionKey $forbidden -}}
{{- fail (printf "smsly.security: secrets.fieldEncryptionKey must be set to a Fernet key (got %q). Generate with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"" .Values.secrets.fieldEncryptionKey) -}}
{{- end -}}
{{- if has .Values.secrets.githubWebhookSecret $forbidden -}}
{{- fail (printf "smsly.security: secrets.githubWebhookSecret must be set (got %q)" .Values.secrets.githubWebhookSecret) -}}
{{- end -}}

{{- /* image tags must be pinned (no "latest" or empty) */ -}}
{{- if has .Values.backend.image.tag (list "latest" "") -}}
{{- fail (printf "smsly.security: backend.image.tag must be pinned (got %q)" .Values.backend.image.tag) -}}
{{- end -}}
{{- if has .Values.frontend.image.tag (list "latest" "") -}}
{{- fail (printf "smsly.security: frontend.image.tag must be pinned (got %q)" .Values.frontend.image.tag) -}}
{{- end -}}
{{- if has .Values.celery.image.tag (list "latest" "") -}}
{{- fail (printf "smsly.security: celery.image.tag must be pinned (got %q)" .Values.celery.image.tag) -}}
{{- end -}}

{{- /* postgresql and redis inline images must also be pinned */ -}}
{{- if has .Values.postgresql.image.tag (list "latest" "") -}}
{{- fail (printf "smsly.security: postgresql.image.tag must be pinned (got %q)" .Values.postgresql.image.tag) -}}
{{- end -}}
{{- /* redis.image is "repository:tag" — extract the tag portion before comparing. */ -}}
{{- $redisTag := last (splitList ":" (default "" .Values.redis.image)) -}}
{{- if has $redisTag (list "latest" "") -}}
{{- fail (printf "smsly.security: redis.image must be pinned to a tag, not \"latest\" or empty (got %q)" .Values.redis.image) -}}
{{- end -}}

{{- /* production-only checks */ -}}
{{- if $prod -}}
{{- /* allowedHosts must not be the wildcard */ -}}
{{- if eq .Values.backend.env.allowedHosts "*" -}}
{{- fail "smsly.security: backend.env.allowedHosts=\"*\" is forbidden in production. Set an explicit comma-separated list of hostnames, e.g. [\"smsly.cloud\",\"www.smsly.cloud\"]." -}}
{{- end -}}
{{- if eq .Values.backend.env.allowedHosts "" -}}
{{- fail "smsly.security: backend.env.allowedHosts is empty in production. Set an explicit comma-separated list of hostnames." -}}
{{- end -}}
{{- /* corsAllowedOrigins must not be the wildcard */ -}}
{{- if eq .Values.backend.env.corsAllowedOrigins "*" -}}
{{- fail "smsly.security: backend.env.corsAllowedOrigins=\"*\" is forbidden in production. Set an explicit comma-separated list of origins." -}}
{{- end -}}
{{- /* Redis: empty password AND no auth is the insecure default we are guarding against.
     In production you must EITHER set secrets.redisPassword OR set redis.auth.enabled=false
     explicitly to acknowledge an unauthenticated Redis instance. */ -}}
{{- if and (has .Values.secrets.redisPassword (list "")) (not .Values.redis.auth.enabled) -}}
{{- fail "smsly.security: redis has no password (secrets.redisPassword=\"\") and redis.auth.enabled=false. In production, either set secrets.redisPassword to a strong value or set redis.auth.enabled=false EXPLICITLY (and document the unauthenticated Redis). The shipping default of both unset is forbidden." -}}
{{- end -}}
{{- end -}}

{{- /* when not using an existingSecret, every password-shaped key under
     backend.env must be non-empty if anyone happens to set it. This catches
     accidental re-introduction of password fields under backend.env. */ -}}
{{- if not .Values.secrets.existingSecret -}}
{{- range $k, $v := .Values.backend.env -}}
{{- if and (hasSuffix "Password" $k) (eq (printf "%v" $v) "") -}}
{{- fail (printf "smsly.security: backend.env.%s must not be empty when secrets.existingSecret is not set. Move it to the secrets.* section." $k) -}}
{{- end -}}
{{- if and (or (eq $k "secretKey") (eq $k "fieldEncryptionKey") (eq $k "githubWebhookSecret")) (eq (printf "%v" $v) "") -}}
{{- fail (printf "smsly.security: backend.env.%s must not be empty when secrets.existingSecret is not set. Move it to the secrets.* section." $k) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}
