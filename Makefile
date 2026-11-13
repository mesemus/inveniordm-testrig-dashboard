# Build and publish the container image (devops.md §1 and §9 "Registry").
#
# The version in pyproject.toml is the one source: the image tag, the chart's appVersion and
# image.tag, /api/health and the UI footer all follow it.
#
#   make bump-version PART=patch|minor|major   raise the version (pyproject.toml, uv.lock, chart, openapi.json)
#   make image-build                           build one platform (PLATFORM) locally, no push
#   make image                                 build amd64 + arm64 and push <version> to Harbor
#
# `make image` refuses: a dirty working tree, a chart that doesn't carry the version, and a
# version that is already in Harbor (release tags are never overwritten: bump instead).
# Credentials for the push come from ~/.profiles/cerit_harbor
# (CERIT_HARBOR_USERNAME, CERIT_HARBOR_PASSWORD), never from this repo.

REGISTRY      ?= cerit.io
HARBOR_PROJECT ?= simekmiroslav
IMAGE_NAME    ?= invenio-testrig-dashboard
PLATFORM      ?= linux/amd64
PLATFORMS     ?= linux/amd64,linux/arm64
HARBOR_ENV    ?= $(HOME)/.profiles/cerit_harbor
CHART         := charts/invenio-testrig-dashboard

# --pull: the newest base images; --no-cache-filter runtime: re-run the runtime stage's apk steps
# (an unchanged RUN line would otherwise keep the packages of the first build forever). The
# frontend and venv stages stay cached: their inputs (package-lock.json, uv.lock) pin them.
BUILD_FLAGS ?= --pull --no-cache-filter runtime

IMAGE   := $(REGISTRY)/$(HARBOR_PROJECT)/$(IMAGE_NAME)
VERSION := $(shell sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -1)
COMMIT  := $(shell git rev-parse HEAD)
DIRTY   := $(shell git status --porcelain --untracked-files=no | grep -q . && echo -dirty)
# Local builds of a changed tree are marked, so they can't be mistaken for the release.
TAG     ?= $(VERSION)$(DIRTY)
LABELS  := --label org.opencontainers.image.version=$(VERSION) \
           --label org.opencontainers.image.revision=$(COMMIT)

.PHONY: image image-build image-check-clean image-check-chart image-check-new image-login bump-version

# A multi-platform build can't be --load-ed and pushed separately, so it builds and pushes in one go.
image: image-check-clean image-check-chart image-login image-check-new
	docker buildx build $(BUILD_FLAGS) $(LABELS) --platform $(PLATFORMS) --push -t $(IMAGE):$(VERSION) .
	@echo "pushed $(IMAGE):$(VERSION) ($(PLATFORMS)), commit $(COMMIT)"
	@echo "next: git tag v$(VERSION) && git push origin v$(VERSION); deploy: devops.md §1"

image-build:
	docker buildx build $(BUILD_FLAGS) $(LABELS) --platform $(PLATFORM) --load -t $(IMAGE):$(TAG) .
	@echo "built $(IMAGE):$(TAG)"

image-check-clean:
	@case "$(DIRTY)" in -dirty) echo "refusing to push: commit your changes first" >&2; exit 1;; esac

image-check-chart:
	@grep -q '^appVersion: "$(VERSION)"$$' $(CHART)/Chart.yaml && grep -q '^  tag: "$(VERSION)"$$' $(CHART)/values.yaml \
	  || { echo "the chart doesn't carry version $(VERSION) (Chart.yaml appVersion, values.yaml image.tag): run make bump-version" >&2; exit 1; }

image-check-new:
	@if docker buildx imagetools inspect $(IMAGE):$(VERSION) >/dev/null 2>&1; then \
	  echo "$(IMAGE):$(VERSION) already exists; release tags are immutable: make bump-version PART=patch" >&2; exit 1; fi

image-login:
	@test -f "$(HARBOR_ENV)" || { echo "missing $(HARBOR_ENV)" >&2; exit 1; }
	@. "$(HARBOR_ENV)" && echo "$$CERIT_HARBOR_PASSWORD" | \
		docker login $(REGISTRY) -u "$$CERIT_HARBOR_USERNAME" --password-stdin

bump-version:
	@case "$(PART)" in patch|minor|major) ;; *) echo "usage: make bump-version PART=patch|minor|major" >&2; exit 1;; esac
	uv version --bump $(PART)
	@new=$$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -1); \
	  sed -i.bak -E "s/^appVersion: .*/appVersion: \"$$new\"/" $(CHART)/Chart.yaml && \
	  sed -i.bak -E "s/^  tag: \".*\"/  tag: \"$$new\"/" $(CHART)/values.yaml && \
	  rm -f $(CHART)/Chart.yaml.bak $(CHART)/values.yaml.bak && \
	  echo "version $$new: pyproject.toml, uv.lock, Chart.yaml appVersion, values.yaml image.tag"
	# openapi.json records the version (CI checks that it is current); the frontend types follow it.
	uv run invenio-testrig-dashboard export-openapi
	cd frontend && npm run -s gen:api
	@echo "now commit, then make image"
