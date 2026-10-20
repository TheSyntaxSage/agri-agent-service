from __future__ import annotations

import logging
from typing import Any, TypedDict
from uuid import uuid4

from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph

from app.config import Settings
from app.decisions import ANALYZERS
from app.demo_data import build_demo_context
from app.java_client import JavaApiUnavailable, JavaInternalApiClient
from app.llm_client import LlmIntentClassifier, LlmUnavailable
from app.models import DecisionOutput, DecisionRequest, IdentityContext, Intent

LOGGER = logging.getLogger(__name__)


class WorkflowError(RuntimeError):
    pass


class LoopDetected(WorkflowError):
    pass


class PermissionDenied(WorkflowError):
    pass


class DecisionState(TypedDict, total=False):
    request: DecisionRequest
    identity: IdentityContext
    trace_id: str
    loop_markers: list[str]
    intent: Intent
    context: dict[str, Any]
    demo: bool
    analysis: dict[str, Any]
    output: DecisionOutput


INTENT_KEYWORDS: list[tuple[Intent, tuple[str, ...]]] = [
    (Intent.PRICE_PROFIT_SENSITIVITY, ("价格", "利润", "调价", "敏感性", "price", "profit")),
    (Intent.SUPPLIER_MATCH, ("供应商", "supplier", "匹配")),
    (Intent.PURCHASE_ADVICE, ("采购", "补货", "purchase", "reorder")),
    (Intent.HARVEST_ADVICE, ("采摘", "采收", "harvest")),
    (Intent.WEATHER_IMPACT, ("天气", "降雨", "温度", "weather")),
    (Intent.PLANTING_ADVICE, ("种植", "作物", "planting", "crop")),
    (Intent.STOCK_ISSUE, ("缺货", "滞销", "积压", "stockout", "slow-moving")),
    (Intent.INVENTORY_COVERAGE, ("覆盖", "库存天数", "inventory coverage")),
    (Intent.SALES_FORECAST, ("销量", "预测", "需求", "forecast", "sales")),
]

ALLOWED_PARAMETERS: dict[Intent, set[str]] = {
    Intent.SALES_FORECAST: {"productName", "forecastDays"},
    Intent.INVENTORY_COVERAGE: {"productName", "leadTimeDays", "safetyStockDays"},
    Intent.STOCK_ISSUE: {"productName", "leadTimeDays", "shelfLifeDays"},
    Intent.PURCHASE_ADVICE: {"productName", "leadTimeDays", "safetyStockDays", "purchaseUnitPrice"},
    Intent.HARVEST_ADVICE: {"productName", "forecastDays", "readyCropQuantity", "wasteRate"},
    Intent.SUPPLIER_MATCH: {"productName"},
    Intent.WEATHER_IMPACT: {"productName", "weather"},
    Intent.PLANTING_ADVICE: {"productName"},
    Intent.PRICE_PROFIT_SENSITIVITY: {"productName", "basePrice", "unitCost", "baseDemand", "priceElasticity"},
}


def recognize_intent(query: str) -> Intent:
    normalized = query.lower().strip()
    scores: list[tuple[int, int, Intent]] = []
    for priority, (intent, keywords) in enumerate(INTENT_KEYWORDS):
        score = sum(1 for keyword in keywords if keyword in normalized)
        if score:
            scores.append((score, -priority, intent))
    if not scores:
        raise WorkflowError("Unable to recognize intent; provide an explicit intent")
    return max(scores)[2]


class DecisionWorkflow:
    LOOP_MARKER = "agri-agent-service:decision"

    def __init__(
        self,
        settings: Settings,
        java_client: JavaInternalApiClient,
        intent_classifier: LlmIntentClassifier | None = None,
    ):
        self.settings = settings
        self.java_client = java_client
        self.intent_classifier = intent_classifier
        graph_builder = StateGraph(DecisionState)
        graph_builder.add_node("guard", self._guard)
        graph_builder.add_node("recognize", self._recognize)
        graph_builder.add_node("authorize", self._authorize)
        graph_builder.add_node("fetch_context", self._fetch_context)
        graph_builder.add_node("analyze", self._analyze)
        graph_builder.add_node("finalize", self._finalize)
        graph_builder.add_edge(START, "guard")
        graph_builder.add_edge("guard", "recognize")
        graph_builder.add_edge("recognize", "authorize")
        graph_builder.add_edge("authorize", "fetch_context")
        graph_builder.add_edge("fetch_context", "analyze")
        graph_builder.add_edge("analyze", "finalize")
        graph_builder.add_edge("finalize", END)
        self.graph = graph_builder.compile(name="agri-decision-workflow")

    async def run(self, request: DecisionRequest, identity: IdentityContext) -> DecisionOutput:
        trace_id = request.trace_id or str(uuid4())
        state = DecisionState(request=request, identity=identity, trace_id=trace_id)
        try:
            result = await self.graph.ainvoke(state, config={"recursion_limit": 10})
        except GraphRecursionError as exc:
            raise LoopDetected("Workflow exceeded LangGraph recursion limit") from exc
        return result["output"]

    async def _guard(self, state: DecisionState) -> dict[str, Any]:
        request: DecisionRequest = state["request"]
        if self.LOOP_MARKER in request.loop_markers:
            raise LoopDetected("Request already contains this agent's loop marker")
        if len(request.loop_markers) >= self.settings.max_agent_hops:
            raise LoopDetected("Maximum agent hop count reached")
        return {"loop_markers": [*request.loop_markers, self.LOOP_MARKER]}

    async def _recognize_intent(self, query: str) -> Intent:
        if query.strip() and self.intent_classifier is not None:
            try:
                intent = await self.intent_classifier.classify(query)
            except LlmUnavailable as exc:
                LOGGER.warning("Intent classification fell back to keyword rules: %s", exc)
            else:
                if intent != Intent.AUTO:
                    return intent
        return recognize_intent(query)

    async def _recognize(self, state: DecisionState) -> dict[str, Any]:
        request: DecisionRequest = state["request"]
        intent = await self._recognize_intent(request.query) if request.intent == Intent.AUTO else request.intent
        unknown = sorted(set(request.parameters) - ALLOWED_PARAMETERS[intent])
        if unknown:
            raise WorkflowError(f"Unsupported parameters for {intent.value}: {', '.join(unknown)}")
        product = request.parameters.get("productName")
        if not isinstance(product, str) or not product.strip():
            raise WorkflowError("productName is required; productId and query text are not used to guess a product")
        from math import isfinite
        for key, value in request.parameters.items():
            if key in {"productName", "weather"}:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
                raise WorkflowError(f"{key} must be a finite number")
            if key != "priceElasticity" and value < 0:
                raise WorkflowError(f"{key} cannot be negative")
            if key in {"forecastDays", "shelfLifeDays"} and (value < 1 or value != int(value)):
                raise WorkflowError(f"{key} must be a positive integer")
            if key == "wasteRate" and value >= 1:
                raise WorkflowError("wasteRate must be less than one")
        return {"intent": intent}

    async def _authorize(self, state: DecisionState) -> dict[str, Any]:
        identity: IdentityContext = state["identity"]
        intent: Intent = state["intent"]
        sensitive = {Intent.PURCHASE_ADVICE: "agent:purchase", Intent.SUPPLIER_MATCH: "agent:supplier"}
        required = sensitive.get(intent)
        if required and "admin" not in identity.roles and required not in identity.permissions:
            raise PermissionDenied(f"{required} permission required for {intent.value}")
        return {}

    async def _fetch_context(self, state: DecisionState) -> dict[str, Any]:
        request: DecisionRequest = state["request"]
        try:
            context = await self.java_client.fetch_decision_context(
                intent=state["intent"],
                identity=state["identity"],
                trace_id=state["trace_id"],
                parameters=request.parameters,
                loop_markers=state["loop_markers"],
            )
            return {"context": context, "demo": False}
        except JavaApiUnavailable:
            if not (self.settings.demo_fallback_enabled and request.allow_demo_fallback):
                raise
            return {"context": build_demo_context(request.parameters), "demo": True}

    async def _analyze(self, state: DecisionState) -> dict[str, Any]:
        intent: Intent = state["intent"]
        analyzer = ANALYZERS[intent]
        return {"analysis": analyzer(state["context"], state["demo"])}

    async def _finalize(self, state: DecisionState) -> dict[str, Any]:
        analysis = dict(state["analysis"])
        analysis.pop("purchase_amount", None)
        output = DecisionOutput(
            intent=state["intent"],
            recommendation=analysis["recommendation"],
            evidence=analysis["evidence"],
            calculations=analysis["calculations"],
            risks=analysis["risks"],
            confidence=analysis["confidence"],
            requires_human_confirmation=True,
            trace_id=state["trace_id"],
            data_mode="test_demo_fallback" if state["demo"] else ("insufficient_data" if analysis["confidence"] == 0 else "live"),
            loop_markers=state["loop_markers"],
        )
        return {"output": output}
