{{/*
Refuse to render with placeholder secrets and unpinned image tags.
Call at the top of every workload template:
  {{- include "smsly.validateValues" . }}
*/}}
{{- define "smsly.validateValues" -}}
{{- $forbidden := list "change-me" "change-me-in-prod" "latest" "" -}}
{{- if has .Values.secrets.secretKey $forbidden -}}
{{- fail (printf "smsly.security: secrets.secretKey must be set (got %q)" .Values.secrets.secretKey) -}}
{{- end -}}
{{- if has .Values.secrets.dbPassword $forbidden -}}
{{- fail (printf "smsly.security: secrets.dbPassword must be set (got %q)" .Values.secrets.dbPassword) -}}
{{- end -}}
{{- if has .Values.secrets.redisPassword $forbidden -}}
{{- fail (printf "smsly.security: secrets.redisPassword must be set (got %q)" .Values.secrets.redisPassword) -}}
{{- end -}}
{{- if has .Values.backend.image.tag (list "latest" "") -}}
{{- fail (printf "smsly.security: backend.image.tag must be pinned (got %q)" .Values.backend.image.tag) -}}
{{- end -}}
{{- if has .Values.frontend.image.tag (list "latest" "") -}}
{{- fail (printf "smsly.security: frontend.image.tag must be pinned (got %q)" .Values.frontend.image.tag) -}}
{{- end -}}
{{- end -}}
