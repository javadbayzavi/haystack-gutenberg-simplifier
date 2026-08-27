# syntax=docker/dockerfile:1

# --- build stage -----------------------------------------------------------
# Dependencies are installed into a self-contained virtualenv here so the
# runtime image never needs a compiler or the package index. Build tooling is
# the largest single source of CVEs in a Python image, and none of it is needed
# to serve a request.
FROM python:3.13-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy only what the dependency resolution needs first, so the expensive layer
# is cached until the dependency set itself changes.
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir .

# --- runtime stage ---------------------------------------------------------
FROM python:3.13-slim AS runtime

# PYTHONUNBUFFERED so structured logs reach the collector as they happen rather
# than sitting in a buffer until the process exits -- which, for a crash, is
# exactly when the logs matter most.
# HAYSTACK_TELEMETRY_ENABLED=False: Haystack's telemetry module runs at *import*
# time and writes a config file into $HOME. A deployed service should not phone
# home regardless, and as a non-root container it cannot -- this crashed the
# image with PermissionError until both this and HOME below were set.
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HAYSTACK_TELEMETRY_ENABLED=False \
    HOME=/home/app \
    HAYHOOKS_HOST=0.0.0.0 \
    HAYHOOKS_PORT=1416

# A fixed uid, not just a name: the Kubernetes securityContext pins runAsUser to
# a number, and the two have to agree.
RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app \
    && mkdir -p /home/app \
    && chown 10001:10001 /home/app

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
# Hayhooks discovers pipelines relative to the working directory.
COPY --chown=10001:10001 pipelines/ ./pipelines/

USER 10001:10001

EXPOSE 1416

# Liveness only. Readiness depends on a secret being mounted, which Docker's
# healthcheck has no way to express -- Kubernetes probes handle that split.
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:1416/health/live', timeout=2).status==200 else 1)"

# Exec form, so uvicorn is PID 1 and receives SIGTERM directly. Wrapped in a
# shell it would not, and every pod eviction would hit the grace-period timeout
# instead of draining.
CMD ["python", "-m", "uvicorn", "gutenberg_simplifier.app:create_application", \
     "--factory", "--host", "0.0.0.0", "--port", "1416"]
