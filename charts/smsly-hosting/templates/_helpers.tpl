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
