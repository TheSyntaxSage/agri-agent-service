from __future__ import annotations

from typing import Any

import httpx

from app.config import Settings
from app.models import Intent


class LlmUnavailable(RuntimeError):
    pass


CLASSIFIABLE_INTENTS: tuple[Intent, ...] = tuple(
    intent for intent in Intent if intent is not Intent.AUTO
)

INSTRUCTIONS = (
    "你是农业供应链决策系统的意图分类器。"
    "从候选意图中选出与用户请求最匹配的一个，只输出该意图的标识字符串本身，"
    "不要输出任何解释、标点或 Markdown 代码块。"
    f"确实无法判断时输出 {Intent.AUTO.value}。"
)


class LlmIntentClassifier:
    """基于 OpenAI Responses API 的意图分类器，仅负责返回意图，不做业务计算。"""

    def __init__(self, settings: Settings, client: httpx.AsyncClient):
        self.settings = settings
        self.client = client

    def _payload(self, query: str) -> dict[str, Any]:
        candidates = "、".join(intent.value for intent in CLASSIFIABLE_INTENTS)
        payload: dict[str, Any] = {
            "model": self.settings.llm_model,
            "instructions": INSTRUCTIONS,
            "input": f"候选意图：{candidates}\n用户请求：{query}",
            "store": False,
        }
        if self.settings.llm_reasoning_effort:
            payload["reasoning"] = {"effort": self.settings.llm_reasoning_effort}
        return payload

    async def classify(self, query: str) -> Intent:
        url = f"{self.settings.llm_base_url.rstrip('/')}/responses"
        try:
            response = await self.client.post(
                url,
                json=self._payload(query),
                headers={"Authorization": f"Bearer {self.settings.llm_api_key}"},
            )
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise LlmUnavailable(f"LLM intent classification unavailable: {type(exc).__name__}") from exc
        if not isinstance(body, dict):
            raise LlmUnavailable("LLM returned a non-object payload")
        return self._parse(self._extract_text(body))

    @staticmethod
    def _extract_text(body: dict[str, Any]) -> str:
        output = body.get("output")
        if not isinstance(output, list):
            raise LlmUnavailable("LLM response contains no output")
        parts: list[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if isinstance(part, dict) and part.get("type") == "output_text" and isinstance(part.get("text"), str):
                    parts.append(part["text"])
        text = "".join(parts).strip()
        if not text:
            raise LlmUnavailable("LLM response contains no output text")
        return text

    @staticmethod
    def _parse(text: str) -> Intent:
        normalized = text.strip().strip("`").strip().strip("\"'").strip().lower().rstrip("。.")
        matched = [intent for intent in Intent if intent.value in normalized]
        if len(matched) == 1:
            return matched[0]
        raise LlmUnavailable(f"LLM returned an unrecognized intent label: {text[:80]!r}")
