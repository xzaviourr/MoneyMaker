"""
/logs — clickable event log for external service calls (Yahoo Finance, 5Paisa).
"""
from __future__ import annotations

from fastapi import APIRouter

from ...shared.service_log import get_logs

router = APIRouter()


@router.get("")
async def list_logs(service: str | None = None, limit: int = 200) -> list[dict]:
    return get_logs(service=service, limit=limit)
