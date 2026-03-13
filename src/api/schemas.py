"""HTTP request schemas for API endpoints."""

from __future__ import annotations

from pydantic import BaseModel, Field


class CreateRoomRequest(BaseModel):
    room_id: str
    host_player_id: str
    end_mode: str


class JoinRoomRequest(BaseModel):
    player_id: str
    is_human: bool = True


class ActionRequest(BaseModel):
    player_id: str
    action_type: str
    payload: dict = Field(default_factory=dict)


class LeaveRoomRequest(BaseModel):
    player_id: str


class ResetRoomRequest(BaseModel):
    player_id: str


class CleanupRoomRequest(BaseModel):
    player_id: str
