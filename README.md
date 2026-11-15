# Invenio Testrig Dashboard

Schedules [invenio-testrig](https://github.com/oarepo/invenio-testrig) runs for every supported
InvenioRDM release line, stores their results (Postgres + S3), shows trends and comparisons, and notifies
maintainers on Discord when tests fail. This README covers development and usage; running the production
deployment is in [devops.md](devops.md).

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (also installs Python 3.14)
- Docker with Compose v2

## Quickstart

```sh
cp config.example.yaml config.yaml
cp .env.example .env
docker compose up -d          # Postgres + RustFS, creates the dev and test buckets
uv run invenio-testrig-dashboard migrate-and-sync   # create/update the schema, sync targets
uv run invenio-testrig-dashboard check-config
uv run pytest
```

`check-config` prints the effective settings as a table with secrets masked. Tests use the
`invenio-testrig-dashboard-test` bucket, so they never touch dev data.

## Inspecting a testrig report

`parse-dir` turns a downloaded testrig report directory into a summary without touching the database
or S3. Add `--tests` to list every parsed test case:

```sh
uv run invenio-testrig-dashboard parse-dir tests/fixtures/runs/real-2026-11-13
uv run invenio-testrig-dashboard parse-dir <report-dir> --tests
```

## Loading a run

`ingest-dir` stores a report directory in Postgres and S3. The target must exist
(`migrate-and-sync` creates it from `config.yaml`); pass `--uid` to re-ingest an existing run:

```sh
uv run invenio-testrig-dashboard ingest-dir tests/fixtures/runs/real-2026-11-13 --target master
```

## Dispatching runs

`dispatch` creates runs for targets whose cron schedule is due and starts them through testrig. Schedules are
cron expressions interpreted in **UTC**. `--dry-run` prints what would happen without changing anything:

```sh
uv run invenio-testrig-dashboard dispatch --dry-run
uv run invenio-testrig-dashboard dispatch
uv run invenio-testrig-dashboard dispatch master    # run one target now, outside its schedule
```

`dispatch <target>` requests a **manual** run of that target and dispatches it immediately, also when today's scheduled
attempts are used up (a failed scheduled dispatch is retried at most 3 times per cron fire). Other targets are left alone.
It's refused (exit 1) for an unknown or disabled target, or when the target already has an active run.

Dispatch calls testrig from the same environment as the dashboard. testrig is a **locked dependency**
(pinned to a commit in `pyproject.toml`), so there is no download at dispatch time and the version is
the same as the one the GitHub workflow runs. To upgrade testrig, change the commit in
`[tool.uv.sources]` and run `uv lock`. To run testrig by hand (the same entry point dispatch uses):

```sh
uv run invenio-testrig github --help
```

## Ingesting runs

`ingest` polls GitHub for active runs, downloads the `final-report` artifact of finished runs
(`queued` → `running` → `completed` / `infra_error` / `lost`) and stores the results. It is safe to
run repeatedly; each run is processed independently.

In Kubernetes the chart's **scheduler pod runs it every 2 minutes**, so a finished run's results appear about two minutes
after it ends. With compose or bare metal, run that loop yourself:

```sh
while sleep 120; do uv run invenio-testrig-dashboard ingest; done
```

One tick by hand:

```sh
uv run invenio-testrig-dashboard ingest
```

It exits 0 when the tick worked (individual runs may still be `infra_error`/`lost`) and 1 on an
authentication or other tick-level failure. Ticks that overlap are normal at this cadence: the second
one finds the lock taken, logs one line and exits.

### Live progress of a running run

While a run is running, each tick also reads GitHub's **job list** (testrig starts one job per package,
and a package job fails when its package fails). Nothing is downloaded for this. The run page then shows
`Progress 34 / 58 packages` with a thin bar, plus `Failed so far: …`; the target page and the targets
overview show `34/58` next to the Running chip. The dashboard already refreshes an active run every
minute, so these numbers move without a reload.

Progress is a hint, not a result: the final report is authoritative (for example, a patched run that
fails while its original passes is counted differently there, and packages can still change until the
run ends). It disappears when the run finishes and the real results take its place.

## Alerts

Messages go to Discord through `ITD_DISCORD_WEBHOOK_URL` (maintainers) and
`ITD_DISCORD_OPS_WEBHOOK_URL` (operators, falling back to the main webhook). Without a webhook, messages
are only logged, so development needs no Discord.

- **Run notifications** (one per run): every completed run with a failed test posts its failed tests, split
  into `New:` and `Still failing:` compared with the target's previous completed run, plus a `Fixed:` section.
  A run without failed tests posts only "all tests pass again" when it fixed the previous run's failures.
  `infra_error`/`lost` runs are skipped when finding the previous run. A message that cannot be posted is retried
  by the next ingest ticks for 24 h.
- **Ops alerts**: `infra_error`/`lost` runs, rejected GitHub tokens or tokens expiring within 14 days, targets
  with no completed run for two schedule intervals, and crashed `dispatch`/`ingest`/`migrate-and-sync` runs.
  Ops alerts are deduplicated with a minimum repeat interval (for example, one crash message per hour).

Links in messages point at `public_url`, so set it to the dashboard's real address.

> **Matching:** ingest matches dispatched runs by the `run-id:` that testrig prints (or, for older
> dispatches, by the run title carrying the uid) and verifies each report's `config_name`. Both need a testrig
> that prints the run id and puts the uid into the run title; the version pinned in `pyproject.toml` does, and the
> same pin is used by the CLI and by the GitHub workflow (`--testrig-branch`).

## Read API and OpenAPI schema

`web` serves the read-only JSON API (steps 9–10 add the SPA on the same app):

```sh
uv run invenio-testrig-dashboard web --reload
```

Open <http://localhost:8000/docs> for Swagger UI. The frontend's TypeScript types are generated from the
schema, so `openapi.json` is committed and CI fails when it is stale:

```sh
uv run invenio-testrig-dashboard export-openapi
```

### Web UI (development)

The React app lives in `frontend/`. Run the API and Vite together; Vite proxies `/api` to `localhost:8000`:

```sh
uv run invenio-testrig-dashboard web --reload   # terminal 1
cd frontend && npm install && npm run dev       # terminal 2
```

`npm run build` writes `frontend/dist`, which `web` serves when it exists (with the client-side history fallback).
Set `frontend_dist` (or `ITD_FRONTEND_DIST`) when the built files live elsewhere, as in the container image.
`npm run gen:api` regenerates `src/api/schema.d.ts` from `openapi.json`; commit the result. Frontend checks:
`npm test`, `npm run lint`, `npm run typecheck`.

## Login and manual re-runs

**Every page needs a GitHub login** (only `/api/health` is open, for probes). Any GitHub user can log in and read; the
only write action is **requesting a run now**, and only GitHub users listed in `maintainers:` may do it.

1. Create a GitHub **OAuth App** (*Settings → Developer settings → OAuth Apps*) with the callback URL
   `<public_url>/api/auth/callback`.
2. Put its id/secret and a long random string into `.env`:

```sh
ITD_GITHUB_OAUTH_CLIENT_ID=...
ITD_GITHUB_OAUTH_CLIENT_SECRET=...
ITD_SESSION_SECRET=<at least 32 random characters>
```

The web server refuses to start without them, and a partial setup is rejected too. For local development without an OAuth
app, set `ITD_REQUIRE_LOGIN=false` (or `require_login: false` in `config.yaml`): everything is then readable anonymously,
the auth endpoints answer 404 and the UI hides the login button. Never use that in production.

For the Vite dev server, set `public_url: http://localhost:5173` and register `http://localhost:5173/api/auth/callback`
in the dev OAuth app; Vite proxies `/api` to the backend (cookies ignore the port).

`public_url` must be the **exact browser-facing URL, scheme included**: with `https://` the session cookie is `Secure`,
and an http browser cannot keep it, so the login looks like a silent loop back to itself (the web log warns about it).

A maintainer sees **Re-run now** on the target and run pages. It creates a run and the web server dispatches it right away (the
scheduled dispatch still picks up anything it misses); a target with an active run refuses with a 409 that links to it. The target
page's runs table and the run page refresh every minute while a run is active.

Who may re-run is the `maintainers:` list: add your GitHub login (case-insensitive) to it in `config.yaml` (the
`ITD_MAINTAINERS` env var takes a JSON list) and restart `web`. With the list empty **nobody sees the button** — that is
the only symptom, so check the list first if it is missing. It appears on the target page and the run detail, not on the
targets overview.

## Host ports

The host-side ports are non-standard so the stack can run next to other local Postgres/S3
instances. Container-internal ports are the standard ones.

| Service | Host port | Container port |
|---|---|---|
| Postgres | `25432` | `5432` |
| S3 API (RustFS) | `29000` | `9000` |
| RustFS web console | `29001` | `9001` |
| Web app (`app` profile) | `8000` | `8000` |

## Running everything in containers

There is **one image** for every process (`Dockerfile`, Alpine-based, about 250 MB); only the command
differs. `docker-compose.yml` puts the app behind profiles, so the whole system runs locally without
Kubernetes. A plain `docker compose up -d` (no profile) still starts only Postgres and RustFS for the
day-to-day loop on the host.

**1. Start the UI:**

```sh
cp -n .env.example .env                   # secrets (skipped if it exists)
cp -n .env.compose.example .env.compose   # container-side addresses (skipped if it exists)
cp -n config.example.yaml config.yaml
docker compose --profile app up -d --build
```

Then open <http://localhost:8000>. This runs `migrate` once and starts `web`: three containers keep
running (`postgres`, `s3`, `web`); `migrate` and `s3-init` exit with code 0 when they're done.
After a code change, run the same `up -d --build` again to rebuild the image.

**2. Choose how runs move forward.** The UI alone doesn't follow runs. "Re-run now" dispatches right
away, but a run stays **`queued`** until an **ingest** tick matches it to its GitHub run, and its
results only appear after another tick once GitHub has finished. Nothing in the `app` profile runs
ingest, so pick one:

- **Follow runs, start nothing on a schedule:** keep an ingest loop running in a second terminal.
  Ingest only reads from GitHub and stores results; it never starts a run.

  ```sh
  while sleep 120; do docker compose run --rm ingest; done
  ```

- **Everything on its schedule:** start the scheduler too (a fourth running container). It runs
  ingest every 2 minutes **and dispatch every 15**, which starts real GitHub runs for every due target.
  Disable targets you don't want run in `config.yaml` (`enabled: false`) first. See "The scheduler"
  below.

  ```sh
  docker compose --profile app --profile scheduler up -d --build
  ```

- **By hand:** `docker compose run --rm ingest` whenever you want an update.

| Service | Command | Profile | Kind |
|---|---|---|---|
| `migrate` | `migrate-and-sync` | `app` | one-shot; `web` waits for it (the same command k8s runs as the init container) |
| `web` | `web --host 0.0.0.0 --port 8000` | `app` | long-running, health-checked on `/api/health`, restarts itself |
| `scheduler` | `supercronic /app/crontab` | `scheduler` (needs `app` too) | long-running; `dispatch` every 15 min, `ingest` every 2 min |
| `dispatch` | `dispatch` | `tools` | one-shot, run by hand |
| `ingest` | `ingest` | `tools` | one-shot, run by hand |

**One-shot commands.** `docker compose run --rm <service>` runs a service once. Naming the service
enables its profile, so no `--profile` flag is needed:

```sh
docker compose run --rm ingest                       # one ingest tick
docker compose run --rm dispatch dispatch --dry-run  # what would be dispatched, without doing it
docker compose run --rm migrate                      # migrations + target sync again (idempotent)
```

Extra arguments **replace** the service's command, so repeat the subcommand (`dispatch dispatch …`).

**The scheduler (opt-in).** It runs `dispatch`, `ingest` and `backup` on the same schedules as the
Helm chart's scheduler pod (`docker/crontab`, UTC; both use supercronic). It dispatches **real GitHub runs** on the runner repo, so only start
it on purpose: a laptop left running would compete with production, and nothing deduplicates that
(each database only knows its own runs). It needs the `app` profile too, because it waits for `migrate`.
Always pass **both** profiles, including when stopping it:

```sh
docker compose --profile app --profile scheduler up -d --build   # start
docker compose --profile app --profile scheduler logs -f scheduler   # watch the ticks
docker compose --profile app --profile scheduler down             # stop everything
```

`docker compose --profile app down` alone leaves a running scheduler **running**, and it keeps
dispatching. Every compose command that names the scheduler needs both profiles, logs included: a plain
`docker compose logs scheduler` fails with "no such service: migrate". Plain Docker works too:
`docker logs -f invenio-testrig-dashboard-scheduler-1`. Stopping it during a dispatch can kill testrig. That run then becomes `lost` after
`lost_after_hours`, the same as when the chart's scheduler pod is stopped mid-dispatch.

**Two environment files.** `.env` holds the secrets and the *host* addresses (`localhost:25432`,
`localhost:29000`). `.env.compose` overrides only what differs *inside* the compose network: the
database at `postgres:5432` and S3 at `s3:9000`. Artifact links are still presigned for
`localhost:29000`, which the browser can reach. Compose loads `.env` first, then `.env.compose`.
Both files are git-ignored.

**`config.yaml` is shared with the host.** Compose mounts the same file read-only. Its `public_url`
must be `http://localhost:8000` for the containers. If you set it to `http://localhost:5173` for the
Vite dev server, the login callback and Discord links point at Vite instead.

**Login.** With no OAuth app, `.env.compose.example` sets `ITD_REQUIRE_LOGIN=false`, so everything is
readable anonymously. That's for local testing only; a real deployment must never set it. To try the
login, put the three OAuth variables from `.env.example` into `.env`, register
`http://localhost:8000/api/auth/callback` in the OAuth app, and delete that line from `.env.compose`.

**Notes**
- testrig is installed **in the image** (a locked dependency, pinned in `pyproject.toml`), so a
  dispatch downloads nothing and runs the same version as the GitHub workflow. A real
  `ITD_GITHUB_TOKEN` is still needed for a dispatch to do anything.
- The image has no `uv`/`uvx` (build-time only) and no `curl`. The web health check uses busybox
  `wget`.
- `down` keeps the volumes; add `-v` to throw away the database and S3 data.

## Backups and restore

`invenio-testrig-dashboard backup` dumps the database with `pg_dump --format=custom` (version 18 or newer, as in the
image) and uploads the dump to `s3://<s3_backup_bucket>/YYYY/MM/DD/invenio-testrig-dashboard-HHMMSS.dump` (UTC).
- **Its own bucket:** `s3_backup_bucket` uses the same S3 credentials, but it must differ from `s3_bucket`. The
  settings refuse the same name, because the artifacts bucket hands out presigned links to browsers.
- **Checked upload:** the stored size must match the dump's.
- **Retention:** after a successful upload, the command deletes its own dumps older than `backup_retention_days`
  (30). It never deletes the dump it just wrote, and it leaves anything else in the bucket alone.
- **Failures:** a failed dump, upload or check exits 1 and posts to the ops Discord. A failed cleanup only warns.
- **The password** reaches `pg_dump` through `PGPASSWORD`, never on its command line.

Where it runs:
- **Kubernetes:** the chart's scheduler pod runs it nightly at 03:00 UTC.
- **Compose:** run `docker compose run --rm backup`, or start the `scheduler` profile, which runs the same 03:00
  schedule. `s3-init` creates the local `invenio-testrig-dashboard-db-backup` bucket. Add `s3_backup_bucket` to your
  `config.yaml` (see `config.example.yaml`).

**Restore:**

```sh
aws s3 ls s3://<s3_backup_bucket>/ --recursive                # pick a dump
aws s3 cp s3://<s3_backup_bucket>/YYYY/MM/DD/<file>.dump .
pg_restore --clean --if-exists --no-owner --dbname=<database url> <file>.dump
```

- **A dump the retention already deleted** survives as an older version, because the CERIT backup bucket is
  versioned:
  `aws s3api list-object-versions --bucket <s3_backup_bucket> --prefix YYYY/MM/DD/`, then
  `aws s3api get-object --bucket … --key … --version-id <id> <file>.dump`.
- **In Kubernetes,** stop the writers first, and restore from a pod that has `pg_restore`; the app image does:
  ```sh
  kubectl -n simek-ns-itd scale deploy/itd-invenio-testrig-dashboard-web --replicas=0
  kubectl -n simek-ns-itd scale deploy/itd-invenio-testrig-dashboard-scheduler --replicas=0
  ```
  After the restore, a `helm upgrade` puts the replicas back (devops.md §5 has the whole procedure).

## Deploying to Kubernetes (Helm)

The chart is in `charts/invenio-testrig-dashboard`. It deploys:
- the web Deployment, with `migrate-and-sync` as its initContainer, plus a Service and an optional Ingress;
- one **scheduler** Deployment: supercronic runs `dispatch` (every 15 min), `ingest` (every 2 min) and `backup`
  (03:00 UTC) in a single long-running pod. Its crontab is rendered from `values.yaml`, and it has the same migrate
  initContainer as web. There are no CronJobs, which would leave a finished pod behind every 2 minutes;
- its own Postgres 18 StatefulSet;
- optional NetworkPolicies.

Every pod passes the *restricted* Pod Security Standard: it runs as a non-root user with a numeric uid, drops all
capabilities, and has a read-only root filesystem with `emptyDir`s at `/tmp` and `HOME`.

1. **Image:** the version in `pyproject.toml` is the image tag. `make bump-version PART=patch|minor|major` raises it
   and copies it into the chart (`appVersion`, `image.tag`). `make image` builds `linux/amd64` + `linux/arm64` and
   pushes `<version>`. It refuses uncommitted changes, a chart without the version, and a version that's already in
   the registry. `/api/health`, the UI footer and `invenio-testrig-dashboard --version` show the running version.
   `make image-build` builds one platform locally without pushing.
2. **Secret:** create it by hand. The chart never creates or reads secrets. Its keys are listed in `values.yaml`
   under `existingSecret`. A missing required key keeps the pods in `CreateContainerConfigError`.
3. **Values:** there's one deployment (CERIT), so `values.yaml` holds its values directly; there's no extra
   values file. The `config:` subtree **is** `config.yaml`.
4. **Install:**
   `helm upgrade --install itd charts/invenio-testrig-dashboard -n simek-ns-itd --reset-values`. `--reset-values`
   matters: without any values flags, Helm reuses the previous revision's stored values over the new `values.yaml`.

What the chart enforces:
- **It refuses** `config.require_login: false`, a leftover `config.testrig_ref`, and a missing `existingSecret` or
  ingress host.
- **Every job runs under `timeout`** (`timeoutSeconds`), and supercronic never overlaps a job with its previous run.
  `dispatch.enabled: false` (the same for `ingest`/`backup`) drops the job from the crontab. A manual run:
  `kubectl exec deploy/itd-invenio-testrig-dashboard-scheduler -- invenio-testrig-dashboard dispatch`.
- **Probes:** readiness uses `/api/health`, which needs the database. Liveness only checks that the port accepts
  connections, so a database outage doesn't restart the web pods.

The CERIT deployment (cluster rules, DNS, TLS, registry, storage) and its first-install runbook are in
[devops.md](devops.md) §9. Day-to-day operations (releasing an image, manual runs, checking and restoring
database dumps, troubleshooting) are in [devops.md](devops.md).

## Configuration

Non-secret settings (repositories, targets, S3 bucket, …) live in `config.yaml`; set
`ITD_CONFIG_FILE` to use a different path. `testrig_config` is an optional **complete** testrig config (stored on every run and shown
publicly, so never put secrets in it) used
for dispatch; leave it unset to use testrig's built-in defaults. Secrets (database URL, GitHub token, S3 keys,
Discord webhooks) come from `ITD_*` environment variables, e.g. via `.env` for local development.

`s3_public_endpoint_url` is the S3 origin browsers use for presigned artifact links. It defaults to
`s3_endpoint_url`; set it when the app reaches S3 at an internal address (e.g. `http://s3:9000`) while browsers need
another one. It must be a **different origin** than `public_url` (uploaded testrig reports contain HTML/SVG files), and
the settings are rejected otherwise.

**The GitHub token** (`ITD_GITHUB_TOKEN`) is a fine-grained personal access token. Give it repository access to
**only the runner repo**, with these permissions:

| Permission | Access | Why |
|---|---|---|
| Actions | Read and write | start workflow runs, read runs, jobs and artifacts |
| Contents | Read and write | testrig clones the runner repo and pushes its updated workflow file |
| **Workflows** | Read and write | GitHub refuses any push that changes `.github/workflows/` without it, even with Contents: write |
| Metadata | Read-only | required by GitHub for every fine-grained token |

Dispatch hands the token to testrig as `GH_TOKEN`. `git push` gets it through `gh`'s credential helper, which dispatch
registers in testrig's environment. So no git or gh configuration is needed on the machine or in the container.

Every setting is listed with a comment in [config.example.yaml](config.example.yaml) (secrets in
[.env.example](.env.example)). Precedence: environment variables > `.env` > `config.yaml` > defaults. Any setting
can be overridden as `ITD_<NAME>` (e.g. `ITD_LOST_AFTER_HOURS=12`).
