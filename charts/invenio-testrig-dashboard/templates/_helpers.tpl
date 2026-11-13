{{/* Names ----------------------------------------------------------------- */}}

{{- define "itd.name" -}}
{{- .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "itd.fullname" -}}
{{- if contains .Chart.Name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{/* Labels. Call with (dict "root" $ "component" "web"). */}}
{{- define "itd.selectorLabels" -}}
app.kubernetes.io/name: {{ include "itd.name" .root }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{- define "itd.labels" -}}
{{ include "itd.selectorLabels" . }}
helm.sh/chart: {{ printf "%s-%s" .root.Chart.Name .root.Chart.Version | replace "+" "_" }}
app.kubernetes.io/version: {{ .root.Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .root.Release.Service }}
{{- /* Every pod that runs the app: the NetworkPolicy lets exactly these reach Postgres. */}}
{{- if ne .component "postgres" }}
invenio-testrig-dashboard/app: "true"
{{- end }}
{{- end -}}

{{/* Checks that make a bad deployment fail at render time ---------------- */}}

{{- define "itd.validate" -}}
{{- if not .Values.existingSecret -}}
{{- fail "existingSecret is required: create the Secret by hand (keys: see values.yaml) and set its name" -}}
{{- end -}}
{{- if and (hasKey .Values.config "require_login") (not .Values.config.require_login) -}}
{{- fail "config.require_login: false is for local development only; the chart refuses it" -}}
{{- end -}}
{{- if hasKey .Values.config "testrig_ref" -}}
{{- fail "config.testrig_ref was removed: the testrig version is pinned in the image (pyproject.toml)" -}}
{{- end -}}
{{- if and .Values.ingress.enabled (not .Values.ingress.host) -}}
{{- fail "ingress.host is required when ingress.enabled" -}}
{{- end -}}
{{- if and .Values.ingress.enabled .Values.ingress.tls.enabled (not .Values.ingress.tls.secretName) -}}
{{- fail "ingress.tls.secretName is required when ingress.tls.enabled" -}}
{{- end -}}
{{- end -}}

{{/* Pod spec pieces shared by every app process ---------------------------- */}}

{{- define "itd.image" -}}
{{ .Values.image.repository }}:{{ .Values.image.tag | default .Chart.AppVersion }}
{{- end -}}

{{- define "itd.configPath" -}}/etc/invenio-testrig-dashboard/config.yaml{{- end -}}

{{/*
Secret keys a container cannot run without. Each is referenced explicitly (besides envFrom),
so a missing key stops the pod with CreateContainerConfigError: the chart can't read the
Secret at render time. Call with (dict "root" $ "web" true/false).
*/}}
{{- define "itd.requiredSecretKeys" -}}
{{- $keys := list "ITD_DATABASE_URL" "ITD_GITHUB_TOKEN" "ITD_S3_ACCESS_KEY" "ITD_S3_SECRET_KEY" -}}
{{- if .root.Values.alerts.enabled -}}
{{- $keys = concat $keys (list "ITD_DISCORD_WEBHOOK_URL" "ITD_DISCORD_OPS_WEBHOOK_URL") -}}
{{- end -}}
{{- if .web -}}
{{- $keys = concat $keys (list "ITD_GITHUB_OAUTH_CLIENT_ID" "ITD_GITHUB_OAUTH_CLIENT_SECRET" "ITD_SESSION_SECRET") -}}
{{- end -}}
{{- toJson $keys -}}
{{- end -}}

{{/* The app container's env, envFrom, security context and mounts. Call with (dict "root" $ "web" bool). */}}
{{- define "itd.appContainerCommon" -}}
image: {{ include "itd.image" .root }}
imagePullPolicy: {{ .root.Values.image.pullPolicy }}
envFrom:
  - secretRef:
      name: {{ .root.Values.existingSecret }}
env:
  - name: ITD_CONFIG_FILE
    value: {{ include "itd.configPath" .root }}
  - name: HOME
    value: /home/app
  {{- range (include "itd.requiredSecretKeys" . | fromJsonArray) }}
  - name: {{ . }}
    valueFrom:
      secretKeyRef:
        name: {{ $.root.Values.existingSecret }}
        key: {{ . }}
  {{- end }}
securityContext:
  {{- toYaml .root.Values.containerSecurityContext | nindent 2 }}
volumeMounts:
  - name: config
    mountPath: {{ include "itd.configPath" .root }}
    subPath: config.yaml
    readOnly: true
  - name: tmp
    mountPath: /tmp
  - name: home
    mountPath: /home/app
{{- end -}}

{{/* Pod-level settings shared by every app pod. */}}
{{- define "itd.appPodCommon" -}}
automountServiceAccountToken: false
# Service links would add env variables for every Service in the namespace.
enableServiceLinks: false
securityContext:
  {{- toYaml .Values.podSecurityContext | nindent 2 }}
{{- with .Values.imagePullSecrets }}
imagePullSecrets:
  {{- toYaml . | nindent 2 }}
{{- end }}
volumes:
  - name: config
    configMap:
      name: {{ include "itd.fullname" . }}
  - name: tmp
    emptyDir: {}
  - name: home
    emptyDir: {}
{{- end -}}

{{/*
A CronJob running one CLI command. Call with
(dict "root" $ "name" "ingest" "values" .Values.ingest "args" (list "ingest") "deadline" 1800).
*/}}
{{- define "itd.cronjob" -}}
{{- $root := .root -}}
apiVersion: batch/v1
kind: CronJob
metadata:
  name: {{ include "itd.fullname" $root }}-{{ .name }}
  labels:
    {{- include "itd.labels" (dict "root" $root "component" .name) | nindent 4 }}
spec:
  schedule: {{ .values.schedule | quote }}
  # The CLI schedules are UTC, like the compose scheduler.
  timeZone: Etc/UTC
  suspend: {{ not .values.enabled }}
  concurrencyPolicy: Forbid
  {{- with .values.startingDeadlineSeconds }}
  startingDeadlineSeconds: {{ . }}
  {{- end }}
  successfulJobsHistoryLimit: {{ .values.successfulJobsHistoryLimit | default 3 }}
  failedJobsHistoryLimit: 3
  jobTemplate:
    spec:
      # We report failures ourselves (ops Discord); k8s retries would duplicate the alerts.
      backoffLimit: 0
      activeDeadlineSeconds: {{ .deadline }}
      template:
        metadata:
          labels:
            {{- include "itd.labels" (dict "root" $root "component" .name) | nindent 12 }}
          annotations:
            checksum/config: {{ include (print $root.Template.BasePath "/configmap.yaml") $root | sha256sum }}
        spec:
          restartPolicy: Never
          {{- include "itd.appPodCommon" $root | nindent 10 }}
          # The CLI turns SIGTERM into a normal exit: it kills testrig and removes its temp dir.
          terminationGracePeriodSeconds: {{ .values.terminationGracePeriodSeconds }}
          containers:
            - name: {{ .name }}
              {{- include "itd.appContainerCommon" (dict "root" $root "web" false) | nindent 14 }}
              args: {{ toJson .args }}
              resources:
                {{- toYaml .values.resources | nindent 16 }}
{{- end -}}
