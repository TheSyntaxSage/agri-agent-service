from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from app.config import Settings
from app.decisions import ANALYZERS
from app.demo_data import build_demo_context
from app.java_client import JavaApiUnavailable, JavaInternalApiClient
from app.models import DecisionOutput, DecisionRequest, IdentityContext, Intent


class WorkflowError(RuntimeError):
    pass


class LoopDetected(WorkflowError):
    pass


class PermissionDenied(WorkflowError):
    pass


Node = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class StateGraph:
    """Small LangGraph-style directed state workflow, with deterministic nodes."""

    END = "__end__"

    def __init__(self) -> None:
        self.nodes: dict[str, Node] = {}
        self.edges: dict[str, str] = {}
        self.entry_point: str | None = None

    def add_node(self, name: str, node: Node) -> None:
        self.nodes[name] = node

    def add_edge(self, source: str, target: str) -> None:
        self.edges[source] = target

    def set_entry_point(self, name: str) -> None:
        self.entry_point = name

    async def invoke(self, state: dict[str, Any], max_steps: int = 10) -> dict[str, Any]:
        current = self.entry_point
        steps = 0
        while current and current != self.END:
            if current not in self.nodes:
                raise WorkflowError(f"Unknown workflow node: {current}")
            if steps >= max_steps:
                raise LoopDetected("Workflow exceeded deterministic step limit")
            state.update(await self.nodes[current](state))
            current = self.edges.get(current, self.END)
            steps += 1
        return state


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

    def __init__(self, settings: Settings, java_client: JavaInternalApiClient | None = None):
        self.settings = settings
        self.java_client = java_client or JavaInternalApiClient(settings)
        self.graph = StateGraph()
        self.graph.add_node("guard", self._guard)
        self.graph.add_node("recognize", self._recognize)
        self.graph.add_node("authorize", self._authorize)
        self.graph.add_node("fetch_context", self._fetch_context)
        self.graph.add_node("analyze", self._analyze)
        self.graph.add_node("finalize", self._finalize)
        self.graph.set_entry_point("guard")
        self.graph.add_edge("guard", "recognize")
        self.graph.add_edge("recognize", "authorize")
        self.graph.add_edge("authorize", "fetch_context")
        self.graph.add_edge("fetch_context", "analyze")
        self.graph.add_edge("analyze", "finalize")
        self.graph.add_edge("finalize", StateGraph.END)

    async def run(self, request: DecisionRequest, identity: IdentityContext) -> DecisionOutput:
        trace_id = request.trace_id or str(uuid4())
        state: dict[str, Any] = {"request": request, "identity": identity, "trace_id": trace_id}
        result = await self.graph.invoke(state)
        return result["output"]

    async def _guard(self, state: dict[str, Any]) -> dict[str, Any]:
        request: DecisionRequest = state["request"]
        if self.LOOP_MARKER in request.loop_markers:
            raise LoopDetected("Request already contains this agent's loop marker")
        if len(request.loop_markers) >= self.settings.max_agent_hops:
            raise LoopDetected("Maximum agent hop count reached")
        return {"loop_markers": [*request.loop_markers, self.LOOP_MARKER]}

    async def _recognize(self, state: dict[str, Any]) -> dict[str, Any]:
        request: DecisionRequest = state["request"]
        intent = recognize_intent(request.query) if request.intent == Intent.AUTO else request.intent
        return {"intent": intent}

    async def _authorize(self, state: dict[str, Any]) -> dict[str, Any]:
        identity: IdentityContext = state["identity"]
        intent: Intent = state["intent"]
        sensitive = {Intent.PURCHASE_ADVICE: "agent:purchase", Intent.SUPPLIER_MATCH: "agent:supplier"}
        required = sensitive.get(intent)
        if required and "admin" not in identity.roles and required not in identity.permissions:
            raise PermissionDenied(f"{required} permission required for {intent.value}")
        return {}

    async def _fetch_context(self, state: dict[str, Any]) -> dict[str, Any]:
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

    async def _analyze(self, state: dict[str, Any]) -> dict[str, Any]:
        intent: Intent = state["intent"]
        analyzer = ANALYZERS[intent]
        return {"analysis": analyzer(state["context"], state["demo"])}

    async def _finalize(self, state: dict[str, Any]) -> dict[str, Any]:
        analysis = dict(state["analysis"])
        purchase_amount = analysis.pop("purchase_amount", 0.0)
        requires_confirmation = bool(analysis["requires_human_confirmation"])
        if purchase_amount >= self.settings.human_confirmation_purchase_amount:
            requires_confirmation = True
        output = DecisionOutput(
            intent=state["intent"],
            recommendation=analysis["recommendation"],
            evidence=analysis["evidence"],
            calculations=analysis["calculations"],
            risks=analysis["risks"],
            confidence=analysis["confidence"],
            requires_human_confirmation=requires_confirmation,
            trace_id=state["trace_id"],
            data_mode="test_demo_fallback" if state["demo"] else "live",
            loop_markers=state["loop_markers"],
        )
        return {"output": output}
