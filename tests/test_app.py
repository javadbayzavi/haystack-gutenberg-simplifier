"""Operational surface: health, auth, limits, request ids, metrics."""

import pytest
from fastapi.testclient import TestClient

from gutenberg_simplifier.app import MAX_BODY_BYTES, REQUEST_ID_HEADER, create_application


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("GUTENBERG_API_TOKEN", raising=False)
    return TestClient(create_application())


@pytest.fixture
def authed_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("GUTENBERG_API_TOKEN", "s3cret-token")
    return TestClient(create_application())


def test_liveness_does_not_depend_on_configuration(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Liveness must not fail for a missing secret; a restart cannot supply one."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json()["status"] == "alive"


def test_readiness_fails_without_an_api_key(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["checks"]["api_key_present"] is False


def test_readiness_reports_each_check_separately(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A single boolean would not say which half is broken."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")

    checks = client.get("/health/ready").json()["checks"]

    assert set(checks) == {"pipeline_loaded", "api_key_present"}
    assert checks["api_key_present"] is True


def test_a_request_id_is_generated_and_returned(client: TestClient) -> None:
    response = client.get("/health/live")

    assert response.headers[REQUEST_ID_HEADER]


def test_an_incoming_request_id_is_preserved(client: TestClient) -> None:
    """A caller's correlation id must survive, or cross-service tracing breaks."""
    response = client.get("/health/live", headers={REQUEST_ID_HEADER: "caller-supplied-id"})

    assert response.headers[REQUEST_ID_HEADER] == "caller-supplied-id"


def test_auth_is_off_when_no_token_is_configured(client: TestClient) -> None:
    assert client.get("/status").status_code == 200


def test_a_configured_token_is_required(authed_client: TestClient) -> None:
    assert authed_client.get("/status").status_code == 401


def test_the_right_token_is_accepted(authed_client: TestClient) -> None:
    response = authed_client.get("/status", headers={"Authorization": "Bearer s3cret-token"})

    assert response.status_code == 200


@pytest.mark.parametrize(
    "header",
    ["Bearer wrong-token", "Basic s3cret-token", "s3cret-token", "Bearer ", ""],
)
def test_malformed_or_wrong_credentials_are_rejected(
    authed_client: TestClient, header: str
) -> None:
    response = authed_client.get("/status", headers={"Authorization": header})

    assert response.status_code == 401


def test_health_stays_reachable_without_a_token(authed_client: TestClient) -> None:
    """A kubelet has no credentials; gating health makes every pod unready."""
    assert authed_client.get("/health/live").status_code == 200
    assert authed_client.get("/health/ready").status_code in (200, 503)


def test_metrics_is_not_exempt_from_auth(authed_client: TestClient) -> None:
    """It reveals request volume and spend, so it is not public."""
    assert authed_client.get("/metrics").status_code == 401


def test_metrics_renders_prometheus_text(client: TestClient) -> None:
    response = client.get("/metrics")

    assert response.status_code == 200
    assert "simplify_requests_total" in response.text


def test_an_oversized_body_is_rejected_early(client: TestClient) -> None:
    response = client.post(
        "/simplify/run",
        content=b"x" * (MAX_BODY_BYTES + 1),
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 413


def test_a_normal_sized_body_is_not_rejected_by_the_limit(client: TestClient) -> None:
    response = client.post("/simplify/run", json={"book_id": 14838})

    # 404 because no pipeline is deployed in this test app -- the point is that
    # the size guard did not fire.
    assert response.status_code != 413
