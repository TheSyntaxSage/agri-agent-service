from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CamelModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True, alias_generator=lambda value: "".join(
        [value.split("_")[0], *[part.capitalize() for part in value.split("_")[1:]]]
    ))


class Intent(str, Enum):
    AUTO = "auto"
    SALES_FORECAST = "sales_forecast"
    INVENTORY_COVERAGE = "inventory_coverage"
    STOCK_ISSUE = "stock_issue"
    PURCHASE_ADVICE = "purchase_advice"
    HARVEST_ADVICE = "harvest_advice"
    SUPPLIER_MATCH = "supplier_match"
    WEATHER_IMPACT = "weather_impact"
    PLANTING_ADVICE = "planting_advice"
    PRICE_PROFIT_SENSITIVITY = "price_profit_sensitivity"


class IdentityContext(CamelModel):
    tenant_id: str
    user_id: str
    roles: set[str] = Field(default_factory=set)
    permissions: set[str] = Field(default_factory=set)


class DecisionRequest(CamelModel):
    query: str = ""
    intent: Intent = Intent.AUTO
    parameters: dict[str, Any] = Field(default_factory=dict)
    allow_demo_fallback: bool = False
    loop_markers: list[str] = Field(default_factory=list)
    trace_id: str | None = None


class Evidence(CamelModel):
    source: str
    description: str
    value: Any | None = None
    demo_data: bool = False


class Calculation(CamelModel):
    metric: str
    formula: str
    result: Any
    unit: str | None = None


class Risk(CamelModel):
    level: str
    description: str
    mitigation: str | None = None


class DecisionOutput(CamelModel):
    intent: Intent
    recommendation: str
    evidence: list[Evidence]
    calculations: list[Calculation]
    risks: list[Risk]
    confidence: float = Field(ge=0.0, le=1.0)
    requires_human_confirmation: bool
    trace_id: str
    data_mode: str = "live"
    loop_markers: list[str] = Field(default_factory=list)


class FeedbackCreate(CamelModel):
    trace_id: str
    intent: Intent
    accepted: bool | None = None
    predicted_value: float | None = None
    actual_value: float | None = None
    comment: str | None = None


class FeedbackRead(FeedbackCreate):
    id: int
    created_at: datetime
    tenant_id: str
    user_id: str


class ErrorMetric(CamelModel):
    intent: Intent
    sample_count: int
    mean_absolute_error: float | None = None
    mean_absolute_percentage_error: float | None = None
    acceptance_rate: float | None = None


class ErrorAnalysis(CamelModel):
    total_feedback: int
    metrics: list[ErrorMetric]


class HealthResponse(CamelModel):
    status: str
    deterministic: bool
    demo_fallback_enabled: bool
