from __future__ import annotations

from typing import Any

import httpx

from app.config import Settings
from app.models import IdentityContext, Intent


class JavaApiUnavailable(RuntimeError):
    pass


class JavaInternalApiClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def fetch_decision_context(
        self,
        *,
        intent: Intent,
        identity: IdentityContext,
        trace_id: str,
        parameters: dict[str, Any],
        loop_markers: list[str],
    ) -> dict[str, Any]:
        headers = {
            "X-Service-Token": self.settings.java_service_token,
            "X-Trace-Id": trace_id,
            "X-Tenant-Id": identity.tenant_id,
            "X-User-Id": identity.user_id,
            "X-Agent-Origin": self.settings.app_name,
            "X-Agent-Hop": str(len(loop_markers)),
            "X-Agent-Loop-Markers": ",".join(loop_markers),
        }
        payload = {"intent": intent.value, "parameters": parameters}
        try:
            async with httpx.AsyncClient(
                base_url=self.settings.java_base_url,
                timeout=self.settings.java_timeout_seconds,
            ) as client:
                response = await client.post("/internal/agent/decision-context", json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise JavaApiUnavailable(f"Java internal API unavailable: {type(exc).__name__}") from exc
        if not isinstance(data, dict):
            raise JavaApiUnavailable("Java internal API returned a non-object payload")
        return data
