from __future__ import annotations

import json

import httpx
import pytest

from app.config import Settings
from app.llm_client import LlmIntentClassifier, LlmUnavailable
from app.models import Intent


def llm_settings(**overrides) -> Settings:
    defaults = {
        "app_env": "test",
        "database_url": "sqlite:///:memory:",
        "llm_base_url": "https://llm.invalid/v1/",
        "llm_api_key": "test-llm-key",
    }
    return Settings(**(defaults | overrides))


def responses_body(text: str) -> dict:
    return {
        "id": "resp_1",
        "status": "completed",
        "output": [
            {"type": "reasoning", "summary": []},
            {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]},
        ],
    }


async def classify(settings: Settings, body: dict, status_code: int = 200) -> tuple[Intent, httpx.Request]:
    captured: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(status_code, json=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0) as http_client:
        classifier = LlmIntentClassifier(settings, http_client)
        intent = await classifier.classify("请给出库存覆盖天数")
    return intent, captured["request"]


@pytest.mark.asyncio
async def test_responses_request_uses_model_gateway_and_no_storage() -> None:
    intent, request = await classify(llm_settings(), responses_body("sales_forecast"))
    assert intent == Intent.SALES_FORECAST
    assert str(request.url) == "https://llm.invalid/v1/responses"
    assert request.headers["authorization"] == "Bearer test-llm-key"
    payload = json.loads(request.content)
    assert payload["model"] == "deepseek-flash"
    assert payload["store"] is False
    assert payload["reasoning"] == {"effort": "high"}
    assert "请给出库存覆盖天数" in payload["input"]
    assert "sales_forecast" in payload["input"]


@pytest.mark.asyncio
async def test_base_url_without_trailing_slash_still_targets_responses_endpoint() -> None:
    _, request = await classify(llm_settings(llm_base_url="https://llm.invalid/v1"), responses_body("sales_forecast"))
    assert str(request.url) == "https://llm.invalid/v1/responses"


@pytest.mark.asyncio
async def test_reasoning_effort_is_omitted_when_configured_empty() -> None:
    _, request = await classify(llm_settings(llm_reasoning_effort=""), responses_body("sales_forecast"))
    assert "reasoning" not in json.loads(request.content)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("label", "expected"),
    [("`sales_forecast`", Intent.SALES_FORECAST), ('"supplier_match"', Intent.SUPPLIER_MATCH), ("stock_issue。", Intent.STOCK_ISSUE)],
)
async def test_loosely_formatted_labels_are_accepted(label: str, expected: Intent) -> None:
    intent, _ = await classify(llm_settings(), responses_body(label))
    assert intent == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [responses_body("不属于任何意图"), responses_body(""), {"output": []}])
async def test_unusable_output_raises_unavailable(body: dict) -> None:
    with pytest.raises(LlmUnavailable):
        await classify(llm_settings(), body)


@pytest.mark.asyncio
async def test_gateway_error_raises_unavailable() -> None:
    with pytest.raises(LlmUnavailable):
        await classify(llm_settings(), {"error": {"message": "boom"}}, status_code=500)
