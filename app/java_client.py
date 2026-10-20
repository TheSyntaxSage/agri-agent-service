from __future__ import annotations

from typing import Any

import httpx

from app.config import Settings
from app.models import IdentityContext, Intent


class JavaApiUnavailable(RuntimeError):
    pass


class JavaInternalApiClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient):
        self.settings = settings
        self.client = client

    def _build_headers(
        self,
        *,
        identity: IdentityContext,
        trace_id: str,
        loop_markers: list[str],
    ) -> dict[str, str]:
        return {
            "X-Service-Token": self.settings.java_service_token,
            "X-Trace-Id": trace_id,
            "X-Tenant-Id": identity.tenant_id,
            "X-User-Id": identity.user_id,
            "X-Agent-Origin": self.settings.app_name,
            "X-Agent-Hop": str(len(loop_markers)),
            "X-Agent-Loop-Markers": ",".join(loop_markers),
        }

    async def _post(
        self,
        path: str,
        *,
        payload: dict[str, Any],
        headers: dict[str, str],
    ) -> dict[str, Any]:
        try:
            response = await self.client.post(path, json=payload, headers=headers)
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise JavaApiUnavailable(f"Java internal API unavailable: {type(exc).__name__}") from exc

        if not isinstance(body, dict):
            raise JavaApiUnavailable("Java internal API returned a non-object payload")

        data = body.get("data")
        if body.get("success") is not True or not isinstance(data, dict):
            raise JavaApiUnavailable("Java internal API returned an invalid envelope")
        return data

    async def fetch_decision_context(
        self,
        *,
        intent: Intent,
        identity: IdentityContext,
        trace_id: str,
        parameters: dict[str, Any],
        loop_markers: list[str],
    ) -> dict[str, Any]:
        return await self._post(
            "/internal/agent/decision-context",
            payload={"intent": intent.value, "parameters": parameters},
            headers=self._build_headers(
                identity=identity,
                trace_id=trace_id,
                loop_markers=loop_markers,
            ),
        )
