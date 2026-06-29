"""Commands route — human overrides published to the MessageBus."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...shared.message_bus import MessageBus
from ...shared.schemas import HumanCommand, Message, MessageType

router = APIRouter()


class CommandRequest(BaseModel):
    command:   str         # "pause_all" | "resume_all" | "emergency_exit" | "set_mode"
    target_id: str = ""
    params:    dict = {}


@router.post("/")
async def send_command(body: CommandRequest) -> dict:
    valid = {"pause_all", "resume_all", "emergency_exit", "set_mode",
             "pause_pod", "resume_pod", "kill_pod"}
    if body.command not in valid:
        raise HTTPException(status_code=400, detail=f"Unknown command: {body.command}")

    cmd = HumanCommand(
        command=body.command,
        target=body.target_id,
        parameters=body.params,
    )
    bus = MessageBus.get()
    await bus.publish(Message(
        type=MessageType.HUMAN_COMMAND,
        payload=cmd.model_dump(),
        source="api.commands",
    ))
    return {"status": "published", "command": body.command}
