"""The deployed application: Hayhooks plus the things a service needs.

Hayhooks builds the pipeline routes; everything an operator needs around them is
added here rather than inside the pipeline wrapper, so it applies to every route
including the ones Hayhooks defines itself.

Hayhooks already ships a request-id middleware and an OTLP tracing bootstrap, so
this is a replacement rather than an invention. Its middleware always mints a
fresh id and ignores an inbound ``X-Request-ID``, which breaks correlation the
moment a caller upstream has already assigned one -- so ours runs outermost and
takes precedence. See :func:`create_application` for the tracing ordering.

Liveness and readiness answer genuinely different questions, and conflating them
is the classic Kubernetes mistake. *Live* means the process is not wedged --
if it fails, restarting helps. *Ready* means this instance can actually serve a
request: the pipeline is deployed and the API key is present. A missing API key
must fail readiness, never liveness: restarting a container cannot conjure a
secret, so a liveness failure there would produce an endless crash loop while a
readiness failure correctly takes the pod out of rotation and leaves it alone.
"""

import os
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from hayhooks import create_app
from hayhooks.server.pipelines import registry

from gutenberg_simplifier import metrics
from gutenberg_simplifier.observability import (
    configure_logging,
    configure_tracing,
    new_request_id,
    request_id_var,
)

REQUEST_ID_HEADER = "X-Request-ID"

#: Reachable without a token. Health checks come from a kubelet that has no
#: credentials, so gating them would make every pod permanently unready.
#: /metrics is deliberately NOT here: it reveals request volume and spend.
_UNAUTHENTICATED_PATHS = frozenset({"/health/live", "/health/ready"})

#: Requests are a book id and a couple of enums. Anything larger is a mistake or
#: an attack, and rejecting it early costs nothing.
MAX_BODY_BYTES = 64 * 1024


def _expected_token() -> str | None:
    """The bearer token, or None when auth is disabled."""
    return os.environ.get("GUTENBERG_API_TOKEN") or None


def create_application() -> FastAPI:
    """Build the full application.

    Order matters. Hayhooks runs its own ``configure_tracing()`` inside
    ``create_app()``, and that call no-ops when tracing is already enabled. Ours
    must therefore run *first* to win; reversed, Hayhooks' OTLP bootstrap would
    install its tracer and ours would silently never be used -- costing the
    request-id correlation and the unconditional content suppression that are
    the reasons for having our own. A test asserts our tracer survives.
    """
    configure_logging()
    configure_tracing(os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"))

    # hayhooks ships no py.typed marker, so create_app() is typed as Any.
    app: FastAPI = create_app()
    _add_middleware(app)
    _add_operational_routes(app)
    return app


def _add_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Assign a request id, enforce the body limit, and check the token."""
        incoming = request.headers.get(REQUEST_ID_HEADER)
        request_id = incoming or new_request_id()
        token = request_id_var.set(request_id)
        try:
            error = _reject(request)
            if error is not None:
                metrics.record_http_error(error.status_code)
                response: Response = error
            else:
                response = await call_next(request)
            response.headers[REQUEST_ID_HEADER] = request_id
            return response
        finally:
            request_id_var.reset(token)


def _reject(request: Request) -> JSONResponse | None:
    """Return an error response if the request must not proceed."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        return JSONResponse(
            status_code=413, content={"detail": f"Request body exceeds {MAX_BODY_BYTES} bytes"}
        )

    expected = _expected_token()
    if expected is None or request.url.path in _UNAUTHENTICATED_PATHS:
        return None

    header = request.headers.get("authorization", "")
    scheme, _, presented = header.partition(" ")
    if scheme.lower() != "bearer" or not _constant_time_equal(presented, expected):
        return JSONResponse(status_code=401, content={"detail": "Invalid or missing bearer token"})
    return None


def _constant_time_equal(presented: str, expected: str) -> bool:
    """Compare without leaking length or prefix through timing."""
    import hmac

    return hmac.compare_digest(presented.encode(), expected.encode())


def _add_operational_routes(app: FastAPI) -> None:
    @app.get("/health/live", include_in_schema=False)
    async def live() -> dict[str, str]:
        """The process is running. Restarting helps if this fails."""
        return {"status": "alive"}

    @app.get("/health/ready", include_in_schema=False)
    async def ready() -> Response:
        """This instance can serve a request.

        A missing key fails readiness rather than liveness: no restart can
        supply a secret, so failing liveness would only cause a crash loop.
        """
        pipelines = registry.get_names()
        checks = {
            "pipeline_loaded": bool(pipelines),
            "api_key_present": bool(os.environ.get("ANTHROPIC_API_KEY")),
        }
        ok = all(checks.values())
        return JSONResponse(
            status_code=200 if ok else 503,
            content={"status": "ready" if ok else "not_ready", "checks": checks},
        )

    @app.get("/metrics", include_in_schema=False)
    async def prometheus_metrics() -> Response:
        payload, content_type = metrics.render()
        return Response(content=payload, media_type=content_type)
