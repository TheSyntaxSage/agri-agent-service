from __future__ import annotations

from fastapi.testclient import TestClient

from app.models import Intent
from app.database import FeedbackRecord


def test_health_is_deterministic(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "deterministic": True, "demoFallbackEnabled": True}


def test_identity_is_required(client: TestClient) -> None:
    response = client.post("/api/v1/decisions", json={"intent": "sales_forecast"})
    assert response.status_code == 401


def test_identity_headers_are_not_trusted_without_valid_service_token(client: TestClient, auth_headers: dict[str, str]) -> None:
    missing = {key: value for key, value in auth_headers.items() if key != "X-Service-Token"}
    assert client.post("/api/v1/decisions", headers=missing, json={"intent": "sales_forecast"}).status_code == 401
    assert client.post("/api/v1/decisions", headers=auth_headers | {"X-Service-Token": "wrong"},
                       json={"intent": "sales_forecast"}).status_code == 401


def test_structured_decision_and_trace_forwarding(client: TestClient, auth_headers: dict[str, str], fake_java) -> None:
    response = client.post(
        "/api/v1/decisions",
        headers=auth_headers,
        json={"query": "预测一下销量", "traceId": "trace-001", "parameters": {"productName": "测试番茄"}},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["intent"] == "sales_forecast"
    assert body["traceId"] == "trace-001"
    assert body["recommendation"]
    assert body["evidence"]
    assert body["calculations"]
    assert body["risks"]
    assert 0 <= body["confidence"] <= 1
    assert body["requiresHumanConfirmation"] is True
    assert body["dataMode"] == "live"
    assert body["loopMarkers"] == ["agri-agent-service:decision"]
    assert fake_java.calls[0]["trace_id"] == "trace-001"


def test_all_supported_intents_return_structured_output(client: TestClient, auth_headers: dict[str, str]) -> None:
    for intent in Intent:
        if intent == Intent.AUTO:
            continue
        response = client.post("/api/v1/decisions", headers=auth_headers,
                               json={"intent": intent.value, "parameters": {"productName": "测试番茄"}})
        assert response.status_code == 200, (intent, response.text)
        body = response.json()
        assert body["intent"] == intent.value
        assert set(("recommendation", "evidence", "calculations", "risks", "confidence", "requiresHumanConfirmation", "traceId")) <= body.keys()


def test_sensitive_intent_needs_extra_permission(client: TestClient, auth_headers: dict[str, str]) -> None:
    headers = auth_headers | {"X-Permissions": "agent:decide"}
    response = client.post("/api/v1/decisions", headers=headers,
                           json={"intent": "purchase_advice", "parameters": {"productName": "测试番茄"}})
    assert response.status_code == 403


def test_loop_marker_is_rejected(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.post(
        "/api/v1/decisions",
        headers=auth_headers,
        json={"intent": "sales_forecast", "parameters": {"productName": "测试番茄"}, "loopMarkers": ["agri-agent-service:decision"]},
    )
    assert response.status_code == 409


def test_feedback_and_error_analysis(client: TestClient, auth_headers: dict[str, str]) -> None:
    payloads = [
        {"traceId": "t1", "intent": "sales_forecast", "accepted": True, "predictedValue": 100, "actualValue": 90},
        {"traceId": "t2", "intent": "sales_forecast", "accepted": False, "predictedValue": 110, "actualValue": 100},
    ]
    for payload in payloads:
        response = client.post("/api/v1/feedback", headers=auth_headers, json=payload)
        assert response.status_code == 201, response.text
    listed = client.get("/api/v1/feedback", headers=auth_headers)
    assert listed.status_code == 200
    assert len(listed.json()) == 2
    analysis = client.get("/api/v1/error-analysis", headers=auth_headers)
    assert analysis.status_code == 200, analysis.text
    metric = analysis.json()["metrics"][0]
    assert analysis.json()["totalFeedback"] == 2
    assert metric["sampleCount"] == 2
    assert metric["meanAbsoluteError"] == 10.0
    assert metric["acceptanceRate"] == 0.5
    other = auth_headers | {"X-Tenant-Id": "tenant-2", "X-User-Id": "user-2"}
    assert client.get("/api/v1/feedback", headers=other).json() == []
    assert client.get("/api/v1/error-analysis", headers=other).json()["totalFeedback"] == 0
    with client.app.state.session_factory() as db:
        db.add(FeedbackRecord(trace_id="legacy-unowned", intent="sales_forecast", accepted=True))
        db.commit()
    assert all(row["traceId"] != "legacy-unowned" for row in client.get("/api/v1/feedback", headers=auth_headers).json())


def test_parameter_whitelist_and_explicit_product(client: TestClient, auth_headers: dict[str, str]) -> None:
    missing = client.post("/api/v1/decisions", headers=auth_headers,
                          json={"query": "预测番茄销量", "intent": "sales_forecast"})
    assert missing.status_code == 422
    unknown = client.post("/api/v1/decisions", headers=auth_headers,
                          json={"intent": "sales_forecast", "parameters": {"productName": "番茄", "madeUp": 1}})
    assert unknown.status_code == 422
