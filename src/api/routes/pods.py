"""Pods routes — list pods, get metrics, send lifecycle commands."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter()

# Pod registry injected at startup
_pod_supervisor: Any = None


def set_pod_supervisor(supervisor: Any) -> None:
    global _pod_supervisor
    _pod_supervisor = supervisor


@router.get("/")
async def list_pods() -> list[dict]:
    if not _pod_supervisor:
        return []
    try:
        return [
            {
                "pod_id":         pod.config.pod_id,
                "name":           pod.config.pod_name,
                "state":          pod.config.state.value,
                "strategy":       pod.config.strategy,
                "capital_budget": str(pod.config.capital_budget),
                "metrics":        pod.get_metrics().model_dump(),
            }
            for pod in _pod_supervisor.pods.values()
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{pod_id}")
async def get_pod(pod_id: str) -> dict:
    if not _pod_supervisor:
        raise HTTPException(status_code=503, detail="pod supervisor not initialised")
    pod = _pod_supervisor.pods.get(pod_id)
    if not pod:
        raise HTTPException(status_code=404, detail=f"Pod {pod_id} not found")
    return {
        "pod_id":  pod.config.pod_id,
        "state":   pod.config.state.value,
        "config":  pod.config.model_dump(),
        "metrics": pod.get_metrics().model_dump(),
    }


@router.post("/{pod_id}/command")
async def send_pod_command(pod_id: str, body: dict) -> dict:
    if not _pod_supervisor:
        raise HTTPException(status_code=503, detail="pod supervisor not initialised")
    action = body.get("action", "")
    if action not in ("pause", "resume", "kill", "review"):
        raise HTTPException(status_code=400, detail=f"Unknown action: {action}")
    try:
        await _pod_supervisor.handle_command(pod_id, action)
        return {"status": "ok", "pod_id": pod_id, "action": action}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
