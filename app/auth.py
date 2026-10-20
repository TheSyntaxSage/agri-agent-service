from __future__ import annotations

from secrets import compare_digest

from fastapi import Header, HTTPException, Request, status

from app.models import IdentityContext


def _split_header(value: str | None) -> set[str]:
    return {part.strip() for part in (value or "").split(",") if part.strip()}


async def require_identity(
    request: Request,
    x_service_token: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None),
    x_roles: str | None = Header(default=None),
    x_permissions: str | None = Header(default=None),
) -> IdentityContext:
    expected = request.app.state.settings.java_service_token
    if expected == "change-me" or not x_service_token or not compare_digest(x_service_token, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid service token")
    if not x_tenant_id or not x_user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing identity headers")
    identity = IdentityContext(
        tenant_id=x_tenant_id,
        user_id=x_user_id,
        roles=_split_header(x_roles),
        permissions=_split_header(x_permissions),
    )
    if "admin" not in identity.roles and "agent:decide" not in identity.permissions:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="agent:decide permission required")
    return identity
