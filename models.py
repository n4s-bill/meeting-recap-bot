from typing import Optional
from pydantic import BaseModel


class WebhookPayload(BaseModel):
    model_config = {"extra": "allow"}

    meeting_id: str
    title: str
    date: str
    participants: Optional[list[str]] = None
    transcript: str
