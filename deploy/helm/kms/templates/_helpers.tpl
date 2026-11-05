{{- define "kms.labels" -}}
app.kubernetes.io/part-of: kms
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end -}}
