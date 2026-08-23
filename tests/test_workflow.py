from __future__ import annotations

import httpx
import pytest

from app.config import Settings
from app.java_client import JavaApiUnavailable, JavaInternalApiClient
from app.models import DecisionRequest, IdentityContext, Intent
from app.workflow import DecisionWorkflow, recognize_intent


class UnavailableClient:
    async def fetch_decision_context(self, **kwargs):
        raise JavaApiUnavailable("offline")


def identity() -> IdentityContext:
    return IdentityContext(
        tenant_id="t",
        user_id="u",
        permissions={"agent:decide", "agent:purchase", "agent:supplier"},
    )


@pytest.mark.asyncio
async def test_demo_fallback_is_explicit_and_test_only(settings: Settings) -> None:
    workflow = DecisionWorkflow(settings, java_client=UnavailableClient())  # type: ignore[arg-type]
    output = await workflow.run(
        DecisionRequest(intent=Intent.SALES_FORECAST, allow_demo_fallback=True, trace_id="demo-trace"),
        identity(),
    )
    assert output.data_mode == "test_demo_fallback"
    assert all(item.demo_data for item in output.evidence)


@pytest.mark.asyncio
async def test_demo_fallback_denied_without_request_opt_in(settings: Settings) -> None:
    workflow = DecisionWorkflow(settings, java_client=UnavailableClient())  # type: ignore[arg-type]
    with pytest.raises(JavaApiUnavailable):
        await workflow.run(DecisionRequest(intent=Intent.SALES_FORECAST), identity())


@pytest.mark.asyncio
async def test_demo_fallback_denied_outside_test(tmp_path) -> None:
    settings = Settings(
        app_env="production",
        database_url=f"sqlite:///{tmp_path / 'prod.db'}",
        allow_test_demo_fallback=True,
    )
    workflow = DecisionWorkflow(settings, java_client=UnavailableClient())  # type: ignore[arg-type]
    with pytest.raises(JavaApiUnavailable):
        await workflow.run(DecisionRequest(intent=Intent.SALES_FORECAST, allow_demo_fallback=True), identity())


def test_intent_recognition_is_deterministic() -> None:
    assert recognize_intent("请给出库存覆盖天数") == Intent.INVENTORY_COVERAGE
    assert recognize_intent("比较供应商并匹配") == Intent.SUPPLIER_MATCH
    assert recognize_intent("分析调价后的利润敏感性") == Intent.PRICE_PROFIT_SENSITIVITY


@pytest.mark.asyncio
async def test_java_client_sends_service_and_trace_headers(monkeypatch, settings: Settings) -> None:
    captured: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        captured["json"] = request.content
        return httpx.Response(200, json={"currentStock": 1})

    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    client = JavaInternalApiClient(settings)
    await client.fetch_decision_context(
        intent=Intent.INVENTORY_COVERAGE,
        identity=identity(),
        trace_id="trace-header",
        parameters={},
        loop_markers=["agri-agent-service:decision"],
    )
    assert captured["headers"]["x-service-token"] == "test-service-token"
    assert captured["headers"]["x-trace-id"] == "trace-header"
    assert captured["headers"]["x-agent-hop"] == "1"
    assert captured["headers"]["x-agent-loop-markers"] == "agri-agent-service:decision"
