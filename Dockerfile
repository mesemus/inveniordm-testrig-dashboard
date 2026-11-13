# syntax=docker/dockerfile:1

# One image for every process: web, dispatch, ingest, migrate-and-sync, the optional supercronic
# scheduler (and, later, backup). Only the command differs (see docker-compose.yml's profiles).
# The dependency set covers the heaviest one: dispatch runs testrig, which needs git and gh.
#
# Alpine: 248 MB on disk instead of 466 MB on Debian slim (measured 2026-11-14). Every
# tool comes from Alpine's signed packages (no extra apt repositories, no checksum-pinned
# downloads), and every compiled
# Python dependency has a musllinux wheel; a missing one fails this build, not a dispatch,
# because testrig is a locked build-time dependency (pyproject.toml, uv.lock).
# Pin the Alpine release with the Python minor: package versions follow it.
ARG PYTHON_IMAGE=python:3.14-alpine3.24

# --- 1. The SPA ----------------------------------------------------------------
FROM node:24-slim AS frontend
# Same major as frontend/.nvmrc and CI (a floating `node:lts` would build with something else).
# Only static files leave this stage, so its base doesn't matter for the image size.
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- 2. The Python environment -------------------------------------------------
# A separate stage so `uv` (and its cache) never reaches the runtime image: testrig is a locked
# dependency, so nothing is downloaded at run time and no uv/uvx is needed there.
# Same base as the runtime: the venv's compiled wheels must match its C library (musl).
FROM ${PYTHON_IMAGE} AS builder
COPY --from=ghcr.io/astral-sh/uv:0.13.8 /uv /bin/uv

# git: testrig is a git dependency. Build-time only here; the runtime has its own.
RUN apk add --no-cache git

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies first, so a code change does not re-resolve them. `--no-editable`: the venv is
# copied to the runtime image on its own, so the project must be a real install inside it, not a
# link to /app/src (which does not exist there). The cache mount stays out of the layer (the
# image once shipped 109 MB of it).
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY src/ ./src/
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-editable

# --- 3. The runtime ------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS runtime

# No ENV TZ: supercronic and the CLI schedules are UTC, like the chart's CronJobs.
# - tini: the CLI is PID 1, and dispatch reaps its own children only; orphaned testrig children
#   would stay zombies. It forwards signals, which the CLI handles.
# - git + github-cli: testrig clones/pushes the runner repo and drives GitHub through `gh`.
#   (Alpine's git needs no Perl; Debian's pulled in 54 MB of it.)
# - ca-certificates: httpx2 verifies TLS against the system store, not certifi.
# - supercronic: the optional compose scheduler; k8s uses the chart's CronJobs.
# - apk upgrade: the base image's own packages lag behind the Alpine repository (zlib 1.3.2 in
#   python:3.14-alpine3.24 when 1.4 was out, 2026-11-15); `apk add` alone never upgrades them.
#   `make image` rebuilds this stage without the layer cache (--no-cache-filter runtime), so the
#   security fixes of the day land in every pushed image.
# - postgresql18-client: pg_dump for `backup`; it must match the server's major version (18).
RUN apk upgrade --no-cache && apk add --no-cache ca-certificates git github-cli postgresql18-client supercronic tini
# The base image's pip (and ensurepip's bundled pip wheel) are never used at run time: our venv
# comes from the builder, has no pip, and testrig's `uv pip` calls run in GitHub Actions. pip
# vendors its own msgpack, urllib3 and setuptools, which image scanners report (Harbor, 2026-11-15:
# msgpack 1.1.2, urllib3 2.7.0, setuptools 70.3.0 with High CVEs), so they go.
RUN rm -rf /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.* \
           /usr/local/lib/python3.*/site-packages/pip \
           /usr/local/lib/python3.*/site-packages/pip-*.dist-info \
           /usr/local/lib/python3.*/ensurepip

WORKDIR /app

# The venv from the builder (same absolute path, so the console scripts' shebangs still work).
COPY --from=builder /app/.venv /app/.venv

# The built SPA. ITD_FRONTEND_DIST is set here because the working directory is not the repo
# root: without it, create_app finds no index.html and serves no SPA at all.
COPY --from=frontend /src/frontend/dist /app/static
# The compose scheduler's schedule (k8s uses the chart's CronJobs instead).
COPY docker/crontab /app/crontab

ENV PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    ITD_FRONTEND_DIST=/app/static

# Not root, and with a writable HOME: dispatch gives testrig a temporary directory under /tmp,
# and git/gh keep their config in HOME.
# The user is numeric (uid:gid), not a name: Kubernetes can only verify runAsNonRoot for a numeric
# USER, and CERIT rejects images without one (devops.md §9). The gid is pinned too, for fsGroup.
RUN addgroup -g 10001 app && adduser -D -u 10001 -G app app
USER 10001:10001

ENTRYPOINT ["tini", "--", "invenio-testrig-dashboard"]
CMD ["web", "--host", "0.0.0.0", "--port", "8000"]
