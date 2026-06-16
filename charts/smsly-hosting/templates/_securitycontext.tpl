{{/*
Common pod-level security context. Apply to every workload.
Usage:
  spec:
    securityContext:
      {{- include "smsly.podSecurityContext" . | nindent 8 }}
*/}}
{{- define "smsly.podSecurityContext" -}}
runAsNonRoot: true
runAsUser: 1000
runAsGroup: 1000
fsGroup: 1000
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
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
runAsNonRoot: true
runAsUser: 1000
capabilities:
  drop:
    - ALL
seccompProfile:
  type: RuntimeDefault
{{- end -}}
