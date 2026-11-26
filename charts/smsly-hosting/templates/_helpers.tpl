{{- define "smsly-hosting.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "smsly-hosting.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{- define "smsly-hosting.labels" -}}
helm.sh/chart: {{ include "smsly-hosting.name" . }}-{{ .Chart.Version | replace "+" "_" }}
{{ include "smsly-hosting.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "smsly-hosting.selectorLabels" -}}
app.kubernetes.io/name: {{ include "smsly-hosting.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "smsly.fullname" -}}
{{- include "smsly-hosting.fullname" . -}}
{{- end }}

{{- define "smsly.labels" -}}
{{- include "smsly-hosting.labels" . -}}
{{- end }}

{{- define "smsly-hosting.secretName" -}}
{{- if .Values.secrets.existingSecret -}}
{{- .Values.secrets.existingSecret -}}
{{- else -}}
{{- printf "%s-env" (include "smsly-hosting.fullname" .) -}}
{{- end -}}
{{- end }}

{{- define "smsly-hosting.dbHost" -}}
{{- if and .Values.externalDatabase .Values.externalDatabase.enabled -}}
{{- .Values.externalDatabase.host -}}
{{- else -}}
{{- printf "%s-postgresql" (include "smsly-hosting.fullname" .) -}}
{{- end -}}
{{- end }}

{{- define "smsly-hosting.dbPort" -}}
{{- if and .Values.externalDatabase .Values.externalDatabase.enabled -}}
{{- default 5432 .Values.externalDatabase.port -}}
{{- else -}}
{{- 5432 -}}
{{- end -}}
{{- end }}

{{- define "smsly-hosting.dbName" -}}
{{- $defaultDb := "smsly_hosting" -}}
{{- if and .Values.postgresql .Values.postgresql.auth .Values.postgresql.auth.database -}}
{{- $defaultDb = .Values.postgresql.auth.database -}}
{{- end -}}
{{- if and .Values.externalDatabase .Values.externalDatabase.enabled -}}
{{- default $defaultDb .Values.externalDatabase.database -}}
{{- else -}}
{{- $defaultDb -}}
{{- end -}}
{{- end }}

{{- define "smsly-hosting.dbUser" -}}
{{- $defaultUser := "smsly_admin" -}}
{{- if and .Values.postgresql .Values.postgresql.auth .Values.postgresql.auth.username -}}
{{- $defaultUser = .Values.postgresql.auth.username -}}
{{- end -}}
{{- if and .Values.externalDatabase .Values.externalDatabase.enabled -}}
{{- default $defaultUser .Values.externalDatabase.username -}}
{{- else -}}
{{- $defaultUser -}}
{{- end -}}
{{- end }}

{{- define "smsly-hosting.redisHost" -}}
{{- if and .Values.externalRedis .Values.externalRedis.enabled -}}
{{- .Values.externalRedis.host -}}
{{- else -}}
{{- printf "%s-redis-master" (include "smsly-hosting.fullname" .) -}}
{{- end -}}
{{- end }}

{{- define "smsly-hosting.redisPort" -}}
{{- if and .Values.externalRedis .Values.externalRedis.enabled -}}
{{- default 6379 .Values.externalRedis.port -}}
{{- else -}}
{{- 6379 -}}
{{- end -}}
{{- end }}

{{- define "smsly-hosting.rabbitmqHost" -}}
{{- if and .Values.externalRabbitmq .Values.externalRabbitmq.enabled -}}
{{- .Values.externalRabbitmq.host -}}
{{- else -}}
{{- printf "%s-rabbitmq" (include "smsly-hosting.fullname" .) -}}
{{- end -}}
{{- end }}

{{- define "smsly-hosting.rabbitmqPort" -}}
{{- if and .Values.externalRabbitmq .Values.externalRabbitmq.enabled -}}
{{- default 5672 .Values.externalRabbitmq.port -}}
{{- else -}}
{{- 5672 -}}
{{- end -}}
{{- end }}

{{- define "smsly-hosting.rabbitmqUser" -}}
{{- if and .Values.externalRabbitmq .Values.externalRabbitmq.enabled -}}
{{- default "smsly_user" .Values.externalRabbitmq.username -}}
{{- else -}}
{{- "smsly_user" -}}
{{- end -}}
{{- end }}

{{- define "smsly-hosting.allowedHosts" -}}
{{- $hosts := default "" .Values.backend.env.allowedHosts -}}
{{- $svc := printf "%s-backend" (include "smsly-hosting.fullname" .) -}}
{{- if eq $hosts "*" -}}
*
{{- else if $hosts -}}
{{- printf "%s,%s,localhost,127.0.0.1" $hosts $svc -}}
{{- else -}}
{{- printf "%s,localhost,127.0.0.1" $svc -}}
{{- end -}}
{{- end }}
