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
{{- if not .Values.secrets.existingSecret -}}
{{- if has (default "" .Values.secrets.secretKey) $forbidden -}}
{{- fail (printf "smsly.security: secrets.secretKey must be set to a strong random value (got %q). Generate with: python -c \"from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())\"" (default "" .Values.secrets.secretKey)) -}}
{{- end -}}
{{- if has (default "" .Values.secrets.dbPassword) $forbidden -}}
{{- fail (printf "smsly.security: secrets.dbPassword must be set (got %q)" (default "" .Values.secrets.dbPassword)) -}}
{{- end -}}
{{- if and .Values.redis .Values.redis.auth .Values.redis.auth.enabled (has (default "" .Values.secrets.redisPassword) $forbidden) -}}
{{- fail (printf "smsly.security: secrets.redisPassword must be set when redis.auth.enabled=true (got %q)" (default "" .Values.secrets.redisPassword)) -}}
{{- end -}}
{{- if has (default "" .Values.secrets.fieldEncryptionKey) $forbidden -}}
{{- fail (printf "smsly.security: secrets.fieldEncryptionKey must be set to a Fernet key (got %q). Generate with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"" (default "" .Values.secrets.fieldEncryptionKey)) -}}
{{- end -}}
{{- if has (default "" .Values.secrets.githubWebhookSecret) $forbidden -}}
{{- fail (printf "smsly.security: secrets.githubWebhookSecret must be set (got %q)" (default "" .Values.secrets.githubWebhookSecret)) -}}
{{- end -}}
{{- if has (default "" .Values.secrets.gatewaySecret) $forbidden -}}
{{- fail (printf "smsly.security: secrets.gatewaySecret must be set (got %q)" (default "" .Values.secrets.gatewaySecret)) -}}
{{- end -}}
{{- if has (default "" .Values.secrets.rabbitmqPassword) $forbidden -}}
{{- fail (printf "smsly.security: secrets.rabbitmqPassword must be set (got %q)" (default "" .Values.secrets.rabbitmqPassword)) -}}
{{- end -}}
{{- end -}}

{{- /* image tags must be pinned (no "latest" or empty) */ -}}
{{- if has (default "" .Values.backend.image.tag) (list "latest" "") -}}
{{- fail (printf "smsly.security: backend.image.tag must be pinned (got %q)" (default "" .Values.backend.image.tag)) -}}
{{- end -}}
{{- if has (default "" .Values.frontend.image.tag) (list "latest" "") -}}
{{- fail (printf "smsly.security: frontend.image.tag must be pinned (got %q)" (default "" .Values.frontend.image.tag)) -}}
{{- end -}}
{{- if has (default "" .Values.celery.image.tag) (list "latest" "") -}}
{{- fail (printf "smsly.security: celery.image.tag must be pinned (got %q)" (default "" .Values.celery.image.tag)) -}}
{{- end -}}

{{- /* postgresql, redis and rabbitmq inline images must also be pinned when enabled */ -}}
{{- if and .Values.postgresql .Values.postgresql.enabled -}}
{{- if has (default "" .Values.postgresql.image.tag) (list "latest" "") -}}
{{- fail (printf "smsly.security: postgresql.image.tag must be pinned (got %q)" (default "" .Values.postgresql.image.tag)) -}}
{{- end -}}
{{- end -}}
{{- if and .Values.redis .Values.redis.enabled -}}
{{- /* redis.image is "repository:tag" — extract the tag portion before comparing. */ -}}
{{- $redisTag := last (splitList ":" (default "" .Values.redis.image)) -}}
{{- if has $redisTag (list "latest" "") -}}
{{- fail (printf "smsly.security: redis.image must be pinned to a tag, not \"latest\" or empty (got %q)" (default "" .Values.redis.image)) -}}
{{- end -}}
{{- end -}}
{{- if and .Values.rabbitmq .Values.rabbitmq.enabled -}}
{{- $rabbitmqTag := last (splitList ":" (default "" .Values.rabbitmq.image)) -}}
{{- if has $rabbitmqTag (list "latest" "") -}}
{{- fail (printf "smsly.security: rabbitmq.image must be pinned to a tag, not \"latest\" or empty (got %q)" (default "" .Values.rabbitmq.image)) -}}
{{- end -}}
{{- end -}}

{{- /* production-only checks */ -}}
{{- if $prod -}}
{{- $allowedHosts := default "" .Values.backend.env.allowedHosts -}}
{{- /* allowedHosts must not be the wildcard */ -}}
{{- if eq $allowedHosts "*" -}}
{{- fail "smsly.security: backend.env.allowedHosts=\"*\" is forbidden in production. Set an explicit comma-separated list of hostnames, e.g. [\"smsly.cloud\",\"www.smsly.cloud\"]." -}}
{{- end -}}
{{- if eq $allowedHosts "" -}}
{{- fail "smsly.security: backend.env.allowedHosts is empty in production. Set an explicit comma-separated list of hostnames." -}}
{{- end -}}
{{- /* corsAllowedOrigins must not be the wildcard */ -}}
{{- if eq (default "" .Values.backend.env.corsAllowedOrigins) "*" -}}
{{- fail "smsly.security: backend.env.corsAllowedOrigins=\"*\" is forbidden in production. Set an explicit comma-separated list of origins." -}}
{{- end -}}
{{- /* Redis: when redis.auth.enabled=true and not using existingSecret,
     secrets.redisPassword must be set in production. When redis.auth.enabled=false
     or secrets.existingSecret is provided, skip inline password requirement. */ -}}
{{- if and (not .Values.secrets.existingSecret) .Values.redis .Values.redis.auth .Values.redis.auth.enabled (has (default "" .Values.secrets.redisPassword) $forbidden) -}}
{{- fail "smsly.security: redis.auth.enabled=true requires secrets.redisPassword to be set in production (or set redis.auth.enabled=false / secrets.existingSecret)." -}}
{{- end -}}
{{- end -}}

{{- /* when not using an existingSecret, every password-shaped key under
     backend.env must be non-empty if anyone happens to set it. This catches
     accidental re-introduction of password fields under backend.env. */ -}}
{{- if not .Values.secrets.existingSecret -}}
{{- range $k, $v := .Values.backend.env -}}
{{- if and (hasSuffix "Password" $k) (eq (printf "%v" (default "" $v)) "") -}}
{{- fail (printf "smsly.security: backend.env.%s must not be empty when secrets.existingSecret is not set. Move it to the secrets.* section." $k) -}}
{{- end -}}
{{- if and (or (eq $k "secretKey") (eq $k "fieldEncryptionKey") (eq $k "githubWebhookSecret")) (eq (printf "%v" (default "" $v)) "") -}}
{{- fail (printf "smsly.security: backend.env.%s must not be empty when secrets.existingSecret is not set. Move it to the secrets.* section." $k) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}
