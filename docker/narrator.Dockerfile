# syntax=docker/dockerfile:1

# The narrator installs only its own extra. It must not carry Haystack, the
# Anthropic client or the OpenTelemetry stack: none are used here, and shipping
# them would mean this image rebuilds and re-scans for CVEs every time the
# simplifier's dependencies move.
FROM python:3.13-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir ".[narrator]"

# --- runtime ---------------------------------------------------------------
FROM python:3.13-slim AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/home/app \
    NARRATOR_HOST=0.0.0.0 \
    NARRATOR_PORT=1417

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app \
    && mkdir -p /home/app \
    && chown 10001:10001 /home/app

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv

USER 10001:10001

EXPOSE 1417

HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:1417/health/live', timeout=2).status==200 else 1)"

# Exec form so uvicorn is PID 1 and gets SIGTERM directly.
CMD ["python", "-m", "uvicorn", "gutenberg_narrator.app:create_application", \
     "--factory", "--host", "0.0.0.0", "--port", "1417"]
