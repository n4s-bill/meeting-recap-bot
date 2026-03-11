from typing import Optional
from pydantic import BaseModel


class Participant(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    permission: Optional[str] = None


class WebhookPayload(BaseModel):
    model_config = {"extra": "allow"}

    meeting_id: str
    title: str
    date: Optional[str] = None
    participants: Optional[list[Participant]] = None
    transcript: str
