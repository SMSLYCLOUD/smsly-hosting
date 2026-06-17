{{- define "smsly-rust-twin.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "smsly-rust-twin.fullname" -}}
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

{{- define "smsly-rust-twin.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "smsly-rust-twin.labels" -}}
helm.sh/chart: {{ include "smsly-rust-twin.chart" . }}
{{ include "smsly-rust-twin.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "smsly-rust-twin.selectorLabels" -}}
app.kubernetes.io/name: {{ include "smsly-rust-twin.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "smsly-rust-twin.componentLabels" -}}
{{- $component := .component -}}
{{- $root := .root -}}
{{ include "smsly-rust-twin.labels" $root }}
app.kubernetes.io/component: {{ $component }}
{{- end }}

{{- define "smsly-rust-twin.componentSelectorLabels" -}}
{{- $component := .component -}}
{{- $root := .root -}}
{{ include "smsly-rust-twin.selectorLabels" $root }}
app.kubernetes.io/component: {{ $component }}
{{- end }}

{{- define "smsly-rust-twin.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "smsly-rust-twin.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{- /*
smsly-rust-twin.securityContext — pod-level security context.
Aligns with the Pod Security Standards "restricted" profile:
  * runAsNonRoot
  * fixed runAsUser / runAsGroup / fsGroup
  * seccompProfile: RuntimeDefault
Applied to every pod template in the chart (api, worker, cli-migrate).
*/ -}}
{{- define "smsly-rust-twin.securityContext" -}}
runAsNonRoot: true
runAsUser: 1000
runAsGroup: 1000
fsGroup: 1000
seccompProfile:
  type: {{ default "RuntimeDefault" .Values.seccompProfile }}
{{- end -}}

{{- /*
smsly-rust-twin.containerSecurityContext — container-level security context.
Aligns with the Pod Security Standards "restricted" profile:
  * no privilege escalation
  * read-only root filesystem (use emptyDir for /tmp if the app needs it)
  * drop ALL Linux capabilities
  * seccompProfile: RuntimeDefault
Applied to every container in the chart.
*/ -}}
{{- define "smsly-rust-twin.containerSecurityContext" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
runAsNonRoot: true
runAsUser: 1000
capabilities:
  drop:
    - ALL
seccompProfile:
  type: {{ default "RuntimeDefault" .Values.seccompProfile }}
{{- end -}}

{{- /*
smsly-rust-twin.image — render a full image reference, optionally pinned by
digest. If `image.digest` is set, the rendered image is `repo:tag@sha256:...`;
otherwise it falls back to `repo:tag` so the chart works out of the box.
*/ -}}
{{- define "smsly-rust-twin.image" -}}
{{- $repo := .Values.image.repository -}}
{{- $tag := .Values.image.tag | default "latest" -}}
{{- if .Values.image.digest -}}
{{ printf "%s:%s@%s" $repo $tag .Values.image.digest }}
{{- else -}}
{{ printf "%s:%s" $repo $tag }}
{{- end -}}
{{- end -}}
