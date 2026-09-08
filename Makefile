# Overridable so CI runs the same targets a developer does:
#   make check PY=python PIP=pip
PY ?= .myenv/bin/python
PIP ?= .myenv/bin/pip

.PHONY: install lint typecheck test test-network eval eval-dry serve check clean docker-build narrator-build kind-load narrator-kind-load helm-lint helm-validate

install:
	$(PIP) install -e ".[all,dev]"

lint:
	$(PY) -m ruff check src tests pipelines evals
	$(PY) -m ruff format --check src tests pipelines evals

format:
	$(PY) -m ruff format src tests pipelines evals
	$(PY) -m ruff check --fix src tests pipelines evals

typecheck:
	$(PY) -m mypy

test:
	$(PY) -m pytest -q

# Hits the real Gutenberg site. Kept out of the default suite on purpose.
test-network:
	$(PY) -m pytest -q -m network

IMAGE ?= gutenberg-simplifier:0.1.0
NARRATOR_IMAGE ?= gutenberg-narrator:0.1.0
KIND_CLUSTER ?= deepset-prep
NAMESPACE ?= gutenberg-simplifier
CHART := deploy/helm/gutenberg-simplifier
NARRATOR_CHART := deploy/helm/gutenberg-narrator

docker-build:
	docker build -f docker/simplifier.Dockerfile -t $(IMAGE) .

# kind has no registry, so the image is side-loaded. Matches the chart's
# imagePullPolicy: IfNotPresent.
narrator-build:
	docker build -f docker/narrator.Dockerfile -t $(NARRATOR_IMAGE) .

kind-load: docker-build
	kind load docker-image $(IMAGE) --name $(KIND_CLUSTER)

narrator-kind-load: narrator-build
	kind load docker-image $(NARRATOR_IMAGE) --name $(KIND_CLUSTER)

helm-lint:
	helm lint $(CHART) --set secrets.anthropicApiKey=dummy
	helm lint $(NARRATOR_CHART)

# Renders and validates against the cluster API without creating anything.
helm-validate:
	helm template gs $(CHART) --set secrets.anthropicApiKey=dummy --set secrets.apiToken=tok \
		| kubectl apply --dry-run=server -f -
	helm template gn $(NARRATOR_CHART) --set engine=hosted --set secrets.ttsApiKey=dummy \
		--set secrets.apiToken=tok | kubectl apply --dry-run=server -f -

# Runs the full application: hayhooks pipelines plus health, metrics and auth.
serve:
	$(PY) -m uvicorn gutenberg_simplifier.app:create_application --factory \
		--host $${HOST:-localhost} --port $${PORT:-1416}

# Measures a real model against the golden set. Needs ANTHROPIC_API_KEY.
eval:
	$(PY) -m evals

# Exercises the harness only -- proves nothing about the model.
eval-dry:
	$(PY) -m evals --dry-run

check: lint typecheck test

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache
	find . -name __pycache__ -type d -not -path "./.myenv/*" -exec rm -rf {} +
