# DevOps manual

Day-to-day operations of the one deployment: CERIT `kuba-cluster`, namespace `simek-ns-itd`, Helm release `itd`,
https://itd.mireksimek.name. The platform facts behind it (cluster rules, DNS, TLS, storage, S3, the first install)
are in [§9 Platform reference](#9-platform-reference-cerit).

## 0. Shell setup (every session)

```sh
export KUBECONFIG=~/.profiles/kuba-cluster.yaml
N=simek-ns-itd
. ~/.profiles/itd_s3                     # ITD_* secrets incl. S3 keys, ITD_PG_PASSWORD
# S3 (CESNET) with s5cmd (brew install s5cmd; v2.3.0 tested): s3 ls / cp / … with the app's keys
s3() { AWS_ACCESS_KEY_ID="$ITD_S3_ACCESS_KEY" AWS_SECRET_ACCESS_KEY="$ITD_S3_SECRET_KEY" AWS_REGION=us-east-1 \
  s5cmd --endpoint-url "$ITD_S3_ENDPOINT_URL" "$@"; }
B=s3://invenio-testrig-dashboard-db-backup
```

Names used below: `itd-invenio-testrig-dashboard-{web,postgres,dispatch,ingest,backup}`.

## 1. Release a new version

The version in `pyproject.toml` is the one source of truth. It's the image tag, the chart's `appVersion` and
`image.tag`, and what `/api/health`, the UI footer and `invenio-testrig-dashboard --version` show.

- [ ] Raise the version: `make bump-version PART=patch` (or `minor`/`major`). It updates `pyproject.toml`, `uv.lock`,
  `Chart.yaml` `appVersion`, `values.yaml` `image.tag`, `openapi.json` and the frontend API types together. It needs
  `npm` on `PATH`.
- [ ] Commit everything (`make image` refuses a dirty tree).
- [ ] `make image` builds amd64 + arm64 and pushes `cerit.io/simekmiroslav/invenio-testrig-dashboard:<version>`, with
  OCI labels for the version and the commit. It refuses:
  - a chart that doesn't carry the version;
  - a version that's **already in Harbor**. Release tags are never overwritten, so bump instead.

  It also rebuilds with the newest base image and Alpine packages, so a patch release with no code changes is how
  security fixes ship.
- [ ] Tag the commit: `git tag v<version> && git push origin v<version>`.
- [ ] Harbor scan clean: https://hub.cerit.io → `simekmiroslav/invenio-testrig-dashboard` → the tag → Vulnerabilities.
- [ ] Optional preview, which persists nothing:
  ```sh
  helm template itd charts/invenio-testrig-dashboard -n $N | kubectl apply --dry-run=server -n $N -f -
  ```
- [ ] Deploy (**always `--reset-values`**: without it, Helm reuses the previous revision's stored values over
  `values.yaml`):
  ```sh
  helm upgrade itd charts/invenio-testrig-dashboard -n $N --reset-values
  ```
- [ ] Verify:
  ```sh
  kubectl -n $N rollout status deploy/itd-invenio-testrig-dashboard-web --timeout=5m
  kubectl -n $N logs deploy/itd-invenio-testrig-dashboard-web -c migrate     # Migrations: X -> Y
  curl -sS https://itd.mireksimek.name/api/health                           # {"status":"ok","version":"<version>"}
  kubectl -n $N get cronjobs -o 'custom-columns=NAME:.metadata.name,IMAGE:.spec.jobTemplate.spec.template.spec.containers[0].image,SUSPEND:.spec.suspend'
  ```
  The footer of every page shows `v<version>` too.
- [ ] Is the rollout stuck in `Init:Error`? See [Troubleshooting](#8-troubleshooting). The old pod keeps serving until
  the new one is ready.

`make image-build` builds one platform locally as `<version>`, or `<version>-dirty` from a changed tree. It never
pushes.

**Rollback:** `helm -n $N history itd`, then `helm -n $N rollback itd <revision>`.
- **Migrations aren't rolled back.** An older image against a newer schema may fail.
- If a release migrated the schema, restore the pre-release dump instead (§5). Take one before a risky release (§4).

## 2. Configuration changes

- **Non-secret settings** (targets, maintainers, schedules, retention): edit `config:` in `values.yaml`, commit,
  `helm upgrade … --reset-values`. The web pod restarts by itself (config checksum). CronJobs read the config on their
  next run. Target changes are synced by the migrate initContainer.
- **Validate locally first.** Copy the `config:` block to a file and run
  `ITD_CONFIG_FILE=<file> uv run invenio-testrig-dashboard check-config` (with `.env` for the secrets).
- **Secrets:** update `~/.profiles/itd_s3`, then re-create the Secret in place. These are the keys the chart requires
  (§9 "Secret"):
  ```sh
  kubectl -n $N create secret generic invenio-testrig-dashboard --dry-run=client -o yaml \
    --from-literal=POSTGRES_PASSWORD="$ITD_PG_PASSWORD" \
    --from-literal=ITD_DATABASE_URL="postgresql://invenio_testrig_dashboard:$ITD_PG_PASSWORD@itd-invenio-testrig-dashboard-postgres:5432/invenio_testrig_dashboard" \
    --from-literal=ITD_GITHUB_TOKEN="$ITD_GITHUB_TOKEN" \
    --from-literal=ITD_S3_ACCESS_KEY="$ITD_S3_ACCESS_KEY" --from-literal=ITD_S3_SECRET_KEY="$ITD_S3_SECRET_KEY" \
    --from-literal=ITD_DISCORD_WEBHOOK_URL="$ITD_DISCORD_WEBHOOK_URL" \
    --from-literal=ITD_DISCORD_OPS_WEBHOOK_URL="$ITD_DISCORD_OPS_WEBHOOK_URL" \
    --from-literal=ITD_GITHUB_OAUTH_CLIENT_ID="$ITD_GITHUB_OAUTH_CLIENT_ID" \
    --from-literal=ITD_GITHUB_OAUTH_CLIENT_SECRET="$ITD_GITHUB_OAUTH_CLIENT_SECRET" \
    --from-literal=ITD_SESSION_SECRET="$ITD_SESSION_SECRET" | kubectl apply -f -
  kubectl -n $N rollout restart deploy/itd-invenio-testrig-dashboard-web   # env is read at pod start
  ```
  - `POSTGRES_PASSWORD` is only used when the database is first initialised. Changing it means `ALTER USER` in
    Postgres (§6) **and** updating both keys.
  - **The GitHub token expires 2027-10-31.** The app warns in ops Discord beforehand. Renew it with the same
    permissions (README "The GitHub token").

## 3. Status and logs

```sh
kubectl -n $N get pods,cronjobs,jobs                      # overview
kubectl -n $N top pods --containers                       # usage (no -w; loop it with watch/while)
kubectl -n $N describe resourcequota                      # quota used vs. hard
kubectl -n $N logs -f deploy/itd-invenio-testrig-dashboard-web            # web
kubectl -n $N logs job/<job-name>                                          # one CronJob run
kubectl -n $N get events --sort-by=.lastTimestamp | tail -20               # scheduling/pull/quota problems
kubectl -n $N get certificate itd-mireksimek-name-tls                      # TLS READY=True
```

Failures of dispatch, ingest, migrate and backup also post to the **ops Discord** channel.

## 4. Manual runs (dispatch, ingest, backup)

A one-off Job from a CronJob gets exactly its image, config and Secret. Job names must be unique; the date suffix keeps
them so. Manual jobs aren't removed by the CronJob history limits, so delete them when done.

```sh
J=backup     # or: ingest, dispatch
kubectl -n $N create job --from=cronjob/itd-invenio-testrig-dashboard-$J $J-manual-$(date +%s)
kubectl -n $N logs -f job/$(kubectl -n $N get jobs -o name | grep "$J-manual" | tail -1 | cut -d/ -f2)
# tidy up: manual jobs only (scheduled ones have no "-manual-" in the name and are pruned by the CronJob)
kubectl -n $N get jobs -o name | grep -- '-manual-' | xargs kubectl -n $N delete
```

- **dispatch** starts **real GitHub Actions runs** for every due target. A brand-new target is due at once. To see
  what it would do, run `kubectl -n $N exec deploy/itd-invenio-testrig-dashboard-web -c web --
  invenio-testrig-dashboard dispatch --dry-run`.
- **One target now:** use "Re-run now" in the UI (maintainers only), or
  `kubectl -n $N exec deploy/itd-invenio-testrig-dashboard-web -c web -- invenio-testrig-dashboard dispatch <branch>`.
- **Pause/resume scheduled dispatching:** set `dispatch.enabled: false/true` in `values.yaml` and upgrade. For a quick
  pause until the next upgrade, use
  `kubectl -n $N patch cronjob itd-invenio-testrig-dashboard-dispatch -p '{"spec":{"suspend":true}}'`.
- **backup** prints `Backup: s3://…/YYYY/MM/DD/…dump (size, seconds)` and `Pruned: N`. Exit 1 means no dump was stored,
  and ops Discord is alerted.

## 5. Database dumps

The nightly backup runs at 03:00 UTC to `s3://invenio-testrig-dashboard-db-backup/YYYY/MM/DD/invenio-testrig-dashboard-HHMMSS.dump`.
Dumps older than 30 days are deleted by the backup itself.
- The bucket has **Object Lock (COMPLIANCE, 30 days)**: nobody can permanently delete a dump during its first 30
  days, not even with the owner key. A deleted dump stays recoverable as an old version, and a lifecycle rule removes
  it 30 days after the delete.
- `s3 rm` on a dump only hides it; deleting a *version* inside its first 30 days fails with `AccessDenied`. That's
  intended, not a bug.

**Check the dumps:**

```sh
s3 ls "$B/*" | tail                                                       # newest last; one per night
kubectl -n $N get jobs | grep backup                                       # last runs: COMPLETIONS 1/1
```

- [ ] There's a dump from last night (UTC), and its size is in line with the previous ones.
- [ ] Monthly: restore one into a scratch database (below, with `--dbname` of a new database) and compare row counts.
  A backup that was never restored isn't a backup.

**Download:** `s3 cp "$B/YYYY/MM/DD/<file>.dump" .`. The file starts with `PGDMP` (custom format).
- **A dump the retention already deleted:** `s3 ls --all-versions "$B/YYYY/MM/DD/*"` lists every version with its id
  (last column). Then run `s3 cp --version-id <id> "$B/YYYY/MM/DD/<file>.dump" .`.

**Restore into the running Postgres.** This **replaces** the data.

```sh
# 1. stop the writers
kubectl -n $N scale deploy/itd-invenio-testrig-dashboard-web --replicas=0
for j in dispatch ingest backup; do kubectl -n $N patch cronjob itd-invenio-testrig-dashboard-$j -p '{"spec":{"suspend":true}}'; done
# 2. copy the dump into the Postgres pod (its /tmp is a writable emptyDir) and restore
kubectl -n $N cp <file>.dump itd-invenio-testrig-dashboard-postgres-0:/tmp/restore.dump
kubectl -n $N exec itd-invenio-testrig-dashboard-postgres-0 -- \
  pg_restore --clean --if-exists --no-owner -U invenio_testrig_dashboard -d invenio_testrig_dashboard /tmp/restore.dump
kubectl -n $N exec itd-invenio-testrig-dashboard-postgres-0 -- rm /tmp/restore.dump
# 3. back to normal (replicas + schedules from values.yaml)
helm upgrade itd charts/invenio-testrig-dashboard -n $N --reset-values
```

## 6. Database access

```sh
kubectl -n $N exec -it itd-invenio-testrig-dashboard-postgres-0 -- psql -U invenio_testrig_dashboard
# e.g.  select status, count(*) from run group by 1;   \dt   \q
```

- **The PVC:** class `nfs-csi-backup` (20 Gi). CERIT keeps its own 30-day offsite copy of the volume, independent of
  our dumps; restoring it means asking CERIT support.
- **Disk usage:**
  `kubectl -n $N exec itd-invenio-testrig-dashboard-postgres-0 -- df -h /var/lib/postgresql`.

## 7. Periodic checklist

- [ ] **Weekly:** last night's dump exists (§5), and `kubectl -n $N get cronjobs` shows recent `LAST SCHEDULE` for
  ingest (≤ 2 min ago) and dispatch (≤ 15 min ago).
- [ ] **Weekly:** ops Discord has no unexplained alerts.
- [ ] **Monthly:**
  - `make image` + deploy, for the base image and Alpine security fixes;
  - check the Harbor scan;
  - a restore test (§5).
- [ ] **Monthly:** quota headroom (`describe resourcequota`), and PVC usage (§6) below ~70 %.
- [ ] **Before 2027-10-31:** a new GitHub token (§2).
- [ ] **Old images in Harbor:** delete tags no revision uses anymore (`helm -n $N history itd`).
- [ ] **Open hardening item:** separate, non-owner S3 credentials for the app (§9 "S3"); request them from CESNET.

## 8. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Web pod `Init:Error`, migrate log `unknown key(s) in …config.yaml` | The config has keys the image doesn't know. Usually an old image tag, e.g. stored values reused by an upgrade without `--reset-values`. Check `helm -n $N get values itd`, then upgrade with `--reset-values`. |
| `Init:Error`, `database not reachable after 120 s` | Postgres isn't ready, or `ITD_DATABASE_URL` is wrong. Check `kubectl -n $N get pod itd-invenio-testrig-dashboard-postgres-0` and its logs. |
| `CreateContainerConfigError` | A key is missing from the Secret (`kubectl -n $N describe pod <pod>`). Re-create it (§2). |
| Pods not created, events `exceeded quota` | The namespace quota is too small (Rancher → simek-ns-itd → quota). |
| Job pod `Error` once, then fine | Look at that job's log. The CronJob retries on its schedule (`backoffLimit: 0` by design: no duplicate alerts). |
| Certificate not `READY` | `kubectl -n $N describe certificate itd-mireksimek-name-tls` and `get challenge`. Delete the Certificate to retry at once. |
| Login loops back to GitHub | `config.public_url` must be exactly `https://itd.mireksimek.name`, and the OAuth app callback `https://itd.mireksimek.name/api/auth/callback`. |

## 9. Platform reference (CERIT)

The facts the chart and the procedures above depend on, checked against the cluster and https://docs.cerit.io in
November 2026.

### Access

| | |
|---|---|
| Rancher | https://rancher.cloud.e-infra.cz (e-INFRA CZ login) |
| Cluster | `kuba-cluster` (RKE2) |
| Kubeconfig | `~/.profiles/kuba-cluster.yaml`, downloaded from Rancher (cluster menu → Download KubeConfig) |
| Namespace | `simek-ns-itd` (Rancher project `p-tdvfb`), for the dashboard only; `simek-ns` is the unused personal default |

- **Create namespaces in the Rancher UI**, not with `kubectl create namespace`: namespaces created with kubectl
  aren't attached to the project quota.
- Your account can't list cluster-scoped objects (namespaces, ingress/gateway classes). Always pass `-n`.

### Pod security

CERIT enforces the *restricted* Pod Security Standard. A workload without these settings is rejected:
- **Pod:** `runAsNonRoot: true`, a **numeric** `runAsUser`, and `seccompProfile: RuntimeDefault`.
- **Every container, init containers included:** `allowPrivilegeEscalation: false` and `capabilities.drop: [ALL]`.

How the chart meets this:
- **The image runs as `10001:10001`.** The Dockerfile's `USER` must stay numeric.
- **App containers** have a read-only root filesystem, with `emptyDir`s at `/tmp` and `/home/app`.
- **Postgres** runs as uid 999 with `fsGroup: 999`, and gets `emptyDir`s at `/var/run/postgresql`, `/tmp` and
  `/dev/shm`.
- **Ports below 1024 are blocked;** the app listens on 8000.

### Quota and resources

- **Namespace quota** (set in Rancher → `simek-ns-itd`): requests 2 CPU / 4000Mi, limits 8 CPU / 12000Mi. It comes out
  of the project quota, which can't be overbooked: lower another namespace first to raise it.
- **The chart's peak**, with web, Postgres, ingest, dispatch and backup running, plus a second web pod during a rolling
  update: ~0.8 CPU / 2 GiB requested, ~6 CPU / 7.5 GiB in limits.
- **Every container needs explicit `resources`.** Without them, the LimitRange gives each one 1 CPU / 512 Mi, and that
  counts against the quota.
- **Exceeding a limit:** too much CPU only throttles. Too much memory or ephemeral storage **evicts the pod**, which is
  why every container has an ephemeral-storage limit.

### Storage

- **Postgres:** a 20 Gi PVC of class **`nfs-csi-backup`**. CERIT keeps offsite copies for 30 days; a restore from
  them needs CERIT support. The class's reclaim policy is `Delete`, so check the PVC before deleting the namespace.
- If Postgres ever fails to initialise on NFS (ownership errors), use `csi-ceph-rbd-du` (block storage) instead.
- The `zfs-csi` class that the CERIT docs recommend for databases doesn't exist on `kuba-cluster`.

### HTTP: DNS, ingress, TLS

- **DNS:** at ClouDNS, `itd.mireksimek.name CNAME kuba-lb.cloud.e-infra.cz`. The CNAME is recommended because the load
  balancer's IP can change. An app behind a CNAME must not send e-mail; this one only posts to Discord.
- **Ingress class `nginx-traefik`,** set **twice**: the `kubernetes.io/ingress.class` annotation *and*
  `spec.ingressClassName`. Different consumers read each one.
- **Certificates:** only the `letsencrypt-gateway` ClusterIssuer works on this cluster. `letsencrypt-prod` shows as
  Ready but never issues. HTTP→HTTPS needs `nginx.ingress.kubernetes.io/ssl-redirect: "true"`.
- **Ignored nginx annotations:** `proxy-body-size` and basic auth (`auth-type`/`auth-secret`) do nothing. This app
  needs neither.
- **A certificate that doesn't issue:**
  `kubectl -n $N describe certificate itd-mireksimek-name-tls` and `kubectl -n $N get challenge`. Delete the
  `Certificate` to retry at once; cert-manager's backoff can be up to an hour.
- **The GitHub OAuth app's callback** must be `https://itd.mireksimek.name/api/auth/callback`, and `config.public_url`
  exactly `https://itd.mireksimek.name`.

### Registry (Harbor)

- **Images:** `cerit.io/simekmiroslav/invenio-testrig-dashboard:<version>`. The UI is at https://hub.cerit.io (e-INFRA
  login), which also shows the vulnerability scans.
- **Push credentials:** the Harbor CLI secret (user profile → CLI secret), in `~/.profiles/cerit_harbor` as
  `CERIT_HARBOR_USERNAME` / `CERIT_HARBOR_PASSWORD`. `make image` logs in with them.
- **The cluster pulls without a pull secret.** By the same token, anyone who knows the name can pull the image, so it
  must never contain secrets. `.dockerignore` keeps `.env*` and `config.yaml` out.

### S3 (CESNET)

Endpoint `https://s3.cl4.du.cesnet.cz`, path-style addressing, region `us-east-1`. The keys are in
`~/.profiles/itd_s3`.

| | `invenio-testrig-dashboard` (artifacts) | `invenio-testrig-dashboard-db-backup` (dumps) |
|---|---|---|
| Access | private; owner-only ACL, no bucket policy | the same |
| Public access block | all four settings on | the same |
| How data leaves | 15-minute presigned GET links handed out by the API (its own origin, separate from the dashboard) | never presigned |
| Versioning | off (artifacts are reproducible) | **Object Lock, COMPLIANCE, 30 days**: no key, not even the owner's, can delete a dump version for 30 days |
| Lifecycle | abort incomplete multipart uploads after 7 days | the same, plus noncurrent versions expire 30 days after the backup's prune, and orphaned delete markers are removed |
| CORS | none (the UI navigates to a redirect, it never fetches S3) | none |

- The keys belong to a Ceph *tenant* user, so an anonymous request can't even address these buckets (404).
- **Open item:** the pods hold the owner keys, which also reach the unrelated `testrepo` bucket. A leaked key can
  delete artifacts, hide dumps (but not destroy them) and change bucket settings. The fix is a separate S3 identity
  for the app with object read/write only, requested from CESNET. The web pod would then only need read access.

### Secret

The chart never creates secrets. The Secret `invenio-testrig-dashboard` must hold:

| Key | Notes |
|---|---|
| `POSTGRES_PASSWORD` | Used only when the database is first initialised. To change it later, `ALTER USER`, then update both keys. Hex (`openssl rand -hex 24`), so it needs no escaping in the URL. |
| `ITD_DATABASE_URL` | `postgresql://invenio_testrig_dashboard:<POSTGRES_PASSWORD>@itd-invenio-testrig-dashboard-postgres:5432/invenio_testrig_dashboard` |
| `ITD_GITHUB_TOKEN` | Fine-grained PAT, runner repo only. Actions, Contents and Workflows read/write. Expires 2027-10-31. |
| `ITD_S3_ACCESS_KEY`, `ITD_S3_SECRET_KEY` | The CESNET keys (endpoint and buckets are in `values.yaml`) |
| `ITD_DISCORD_WEBHOOK_URL`, `ITD_DISCORD_OPS_WEBHOOK_URL` | `#itd-notification` (run notifications) and the ops channel |
| `ITD_GITHUB_OAUTH_CLIENT_ID`, `ITD_GITHUB_OAUTH_CLIENT_SECRET`, `ITD_SESSION_SECRET` | Web pod only. The session secret is ≥ 32 characters. |

A missing required key keeps the pods in `CreateContainerConfigError`. The chart references every key explicitly
for that reason, so a half-configured pod never starts.

### Network

- **NetworkPolicies** (`networkPolicy.enabled`):
  - web accepts traffic only from `kube-system`, where the ingress controller runs, and from this release's pods;
  - Postgres accepts only pods labelled `invenio-testrig-dashboard/app: "true"`, i.e. the app pods.
- **Egress is open.** The app needs GitHub (testrig, `gh`), Discord and CESNET S3.

### First install (done 2026-11-15; for a rebuild from scratch)

1. **Namespace and quota** in Rancher (above).
2. **The Secret:** the command in §2, with `kubectl create` instead of `--dry-run … | apply`.
3. **Preview:** the server-side dry run in §1.
4. **Install:** `helm upgrade --install itd charts/invenio-testrig-dashboard -n $N --reset-values`.
5. **Watch it come up:** PVC bound → Postgres ready → the migrate initContainer (it waits up to 120 s for the
   database) → web ready → certificate issued:
   `kubectl -n $N get pods,pvc,ingress,certificate`.
6. **Check:**
   - `curl https://itd.mireksimek.name/api/health`;
   - log in through GitHub;
   - one manual ingest and one manual backup (§4);
   - egress:
     ```sh
     kubectl -n $N exec deploy/itd-invenio-testrig-dashboard-web -c web -- python -c "import httpx2
     for u in ('https://api.github.com', 'https://discord.com/api/v10/gateway', 'https://s3.cl4.du.cesnet.cz'):
         print(u, httpx2.get(u, timeout=10).status_code)"
     ```

