from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.demo_data import build_demo_context
from app.main import create_app
from app.workflow import DecisionWorkflow


class FakeJavaClient:
    def __init__(self, overrides: dict | None = None):
        self.overrides = overrides or {}
        self.calls: list[dict] = []

    async def fetch_decision_context(self, **kwargs):
        self.calls.append(kwargs)
        return build_demo_context(self.overrides | kwargs["parameters"])


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        app_env="test",
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        java_base_url="http://java.invalid",
        java_service_token="test-service-token",
        allow_test_demo_fallback=True,
    )


@pytest.fixture
def fake_java() -> FakeJavaClient:
    return FakeJavaClient()


@pytest.fixture
def client(settings: Settings, fake_java: FakeJavaClient) -> Iterator[TestClient]:
    workflow = DecisionWorkflow(settings, java_client=fake_java)  # type: ignore[arg-type]
    with TestClient(create_app(settings, workflow)) as test_client:
        yield test_client


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {
        "X-Tenant-Id": "tenant-1",
        "X-User-Id": "user-1",
        "X-Roles": "manager",
        "X-Permissions": "agent:decide,agent:purchase,agent:supplier",
    }

