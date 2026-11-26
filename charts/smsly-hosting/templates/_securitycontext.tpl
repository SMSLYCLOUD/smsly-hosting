{{/*
Common pod-level security context. Apply to every workload.
Usage:
  spec:
    securityContext:
      {{- include "smsly.podSecurityContext" . | nindent 8 }}
*/}}
{{- define "smsly.podSecurityContext" -}}
{{- $ctx := default (dict) . -}}
{{- $uid := default 1000 $ctx.runAsUser -}}
{{- $gid := default 1000 $ctx.runAsGroup -}}
{{- $fsGroup := default $gid $ctx.fsGroup -}}
runAsNonRoot: true
runAsUser: {{ $uid }}
runAsGroup: {{ $gid }}
fsGroup: {{ $fsGroup }}
seccompProfile:
  type: RuntimeDefault
{{- end -}}

{{/*
Common container-level security context.
Usage:
  containers:
    - name: foo
      securityContext:
        {{- include "smsly.containerSecurityContext" . | nindent 8 }}
*/}}
{{- define "smsly.containerSecurityContext" -}}
{{- $ctx := default (dict) . -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
runAsNonRoot: true
runAsUser: {{ default 1000 $ctx.runAsUser }}
capabilities:
  drop:
    - ALL
seccompProfile:
  type: RuntimeDefault
{{- end -}}
