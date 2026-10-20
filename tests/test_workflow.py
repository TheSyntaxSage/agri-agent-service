from __future__ import annotations

import httpx
import pytest

from app.config import Settings
from app.decisions import _forecast, price_profit_sensitivity, weather_impact
from app.java_client import JavaApiUnavailable, JavaInternalApiClient
from app.llm_client import LlmUnavailable
from app.models import DecisionRequest, IdentityContext, Intent
from app.workflow import DecisionWorkflow, WorkflowError, recognize_intent


class UnavailableClient:
    async def fetch_decision_context(self, **kwargs):
        raise JavaApiUnavailable("offline")


class FakeIntentClassifier:
    def __init__(self, intent: Intent | None = None, error: Exception | None = None):
        self.intent = intent
        self.error = error
        self.queries: list[str] = []

    async def classify(self, query: str) -> Intent:
        self.queries.append(query)
        if self.error is not None:
            raise self.error
        assert self.intent is not None
        return self.intent


def identity() -> IdentityContext:
    return IdentityContext(
        tenant_id="t",
        user_id="u",
        permissions={"agent:decide", "agent:purchase", "agent:supplier"},
    )


def test_workflow_uses_official_langgraph(settings: Settings) -> None:
    workflow = DecisionWorkflow(settings, java_client=UnavailableClient())  # type: ignore[arg-type]
    assert workflow.graph.__class__.__module__.startswith("langgraph.")
    graph = workflow.graph.get_graph()
    assert {"guard", "recognize", "authorize", "fetch_context", "analyze", "finalize"}.issubset(graph.nodes)


@pytest.mark.asyncio
async def test_demo_fallback_is_explicit_and_test_only(settings: Settings) -> None:
    workflow = DecisionWorkflow(settings, java_client=UnavailableClient())  # type: ignore[arg-type]
    output = await workflow.run(
        DecisionRequest(intent=Intent.SALES_FORECAST, parameters={"productName": "测试番茄"}, allow_demo_fallback=True, trace_id="demo-trace"),
        identity(),
    )
    assert output.data_mode == "test_demo_fallback"
    assert all(item.demo_data for item in output.evidence)


@pytest.mark.asyncio
async def test_demo_fallback_denied_without_request_opt_in(settings: Settings) -> None:
    workflow = DecisionWorkflow(settings, java_client=UnavailableClient())  # type: ignore[arg-type]
    with pytest.raises(JavaApiUnavailable):
        await workflow.run(DecisionRequest(intent=Intent.SALES_FORECAST, parameters={"productName": "测试番茄"}), identity())


@pytest.mark.asyncio
async def test_demo_fallback_denied_outside_test() -> None:
    settings = Settings(
        app_env="production",
        database_url="sqlite:///:memory:",
        allow_test_demo_fallback=True,
    )
    workflow = DecisionWorkflow(settings, java_client=UnavailableClient())  # type: ignore[arg-type]
    with pytest.raises(JavaApiUnavailable):
        await workflow.run(DecisionRequest(intent=Intent.SALES_FORECAST, parameters={"productName": "测试番茄"}, allow_demo_fallback=True), identity())


def test_intent_recognition_is_deterministic() -> None:
    assert recognize_intent("请给出库存覆盖天数") == Intent.INVENTORY_COVERAGE
    assert recognize_intent("比较供应商并匹配") == Intent.SUPPLIER_MATCH
    assert recognize_intent("分析调价后的利润敏感性") == Intent.PRICE_PROFIT_SENSITIVITY


def build_workflow(settings: Settings, classifier: FakeIntentClassifier) -> DecisionWorkflow:
    return DecisionWorkflow(settings, java_client=UnavailableClient(), intent_classifier=classifier)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_llm_classifies_query_without_keywords(settings: Settings) -> None:
    classifier = FakeIntentClassifier(intent=Intent.SALES_FORECAST)
    workflow = build_workflow(settings, classifier)
    output = await workflow.run(
        DecisionRequest(query="番茄最近卖得怎么样", parameters={"productName": "测试番茄"}, allow_demo_fallback=True),
        identity(),
    )
    assert output.intent == Intent.SALES_FORECAST
    assert classifier.queries == ["番茄最近卖得怎么样"]


@pytest.mark.asyncio
async def test_llm_failure_falls_back_to_keyword_rules(settings: Settings) -> None:
    classifier = FakeIntentClassifier(error=LlmUnavailable("gateway timeout"))
    workflow = build_workflow(settings, classifier)
    output = await workflow.run(
        DecisionRequest(query="请给出库存覆盖天数", parameters={"productName": "测试番茄"}, allow_demo_fallback=True),
        identity(),
    )
    assert output.intent == Intent.INVENTORY_COVERAGE


@pytest.mark.asyncio
async def test_llm_failure_without_keywords_still_reports_unrecognized_intent(settings: Settings) -> None:
    classifier = FakeIntentClassifier(error=LlmUnavailable("gateway timeout"))
    workflow = build_workflow(settings, classifier)
    with pytest.raises(WorkflowError):
        await workflow.run(
            DecisionRequest(query="番茄最近卖得怎么样", parameters={"productName": "测试番茄"}, allow_demo_fallback=True),
            identity(),
        )


@pytest.mark.asyncio
async def test_llm_auto_answer_falls_back_to_keyword_rules(settings: Settings) -> None:
    classifier = FakeIntentClassifier(intent=Intent.AUTO)
    workflow = build_workflow(settings, classifier)
    output = await workflow.run(
        DecisionRequest(query="请给出库存覆盖天数", parameters={"productName": "测试番茄"}, allow_demo_fallback=True),
        identity(),
    )
    assert output.intent == Intent.INVENTORY_COVERAGE


@pytest.mark.asyncio
async def test_llm_is_not_called_for_explicit_intent_or_blank_query(settings: Settings) -> None:
    classifier = FakeIntentClassifier(intent=Intent.WEATHER_IMPACT)
    workflow = build_workflow(settings, classifier)
    explicit = await workflow.run(
        DecisionRequest(intent=Intent.SALES_FORECAST, parameters={"productName": "测试番茄"}, allow_demo_fallback=True),
        identity(),
    )
    assert explicit.intent == Intent.SALES_FORECAST
    with pytest.raises(WorkflowError):
        await workflow.run(
            DecisionRequest(query="   ", parameters={"productName": "测试番茄"}, allow_demo_fallback=True),
            identity(),
        )
    assert classifier.queries == []


def test_sales_are_daily_aggregated_sorted_and_use_recent_window() -> None:
    daily, trend, window = _forecast({"salesHistory": [
        {"date": "2026-09-08", "quantityKg": 3}, {"date": "2026-09-07", "quantityKg": 2},
        {"date": "2026-09-08", "quantityKg": 4}, {"date": "2026-09-06", "quantityKg": 1},
    ]})
    assert window == [1.0, 2.0, 7.0]
    assert trend == 3.0
    assert daily > 0


def test_missing_weather_and_price_do_not_fabricate_normal_advice() -> None:
    weather = weather_impact({"productName": "番茄"}, False)
    invalid_weather = weather_impact({"productName": "番茄", "weather": {
        "maxTemp": None, "minTemp": "", "rainfallMm": "unknown", "windKph": False,
    }}, False)
    pricing = price_profit_sensitivity({"productName": "番茄"}, False)
    assert weather["confidence"] == 0.0 and "数据不足" in weather["recommendation"]
    assert invalid_weather["confidence"] == 0.0 and "数据不足" in invalid_weather["recommendation"]
    assert pricing["confidence"] == 0.0 and "数据不足" in pricing["recommendation"]


@pytest.mark.asyncio
async def test_java_client_sends_service_and_trace_headers(settings: Settings) -> None:
    captured: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        captured["json"] = request.content
        return httpx.Response(200, json={"success": True, "data": {"currentStock": 1}})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        base_url=settings.java_base_url,
        timeout=settings.java_timeout_seconds,
        transport=transport,
    ) as http_client:
        client = JavaInternalApiClient(settings, http_client)
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
