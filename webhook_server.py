import json
import logging
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import ValidationError

import config
from models import WebhookPayload
from pipeline import process_meeting, ProcessingStatus

logger = logging.getLogger(__name__)

app = FastAPI(title="Meeting Recap Bot")


def _authenticate(x_webhook_secret: Optional[str], authorization: Optional[str]) -> bool:
    expected = config.WEBHOOK_SECRET
    if x_webhook_secret and x_webhook_secret == expected:
        return True
    if authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() == "bearer" and token == expected:
            return True
    return False


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.post("/webhook/transcript")
async def receive_transcript(
    request: Request,
    x_webhook_secret: Optional[str] = Header(default=None),
    authorization: Optional[str] = Header(default=None),
):
    client_host = request.client.host if request.client else "unknown"

    if not _authenticate(x_webhook_secret, authorization):
        logger.warning("Webhook auth failed from %s", client_host)
        raise HTTPException(status_code=401, detail="Unauthorized")

    raw_body = await request.body()
    try:
        raw_json = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        logger.error("Invalid JSON from %s: %s", client_host, exc)
        raise HTTPException(status_code=422, detail="Invalid JSON")

    logger.info("Raw webhook payload: %s", json.dumps(raw_json, default=str)[:2000])

    try:
        payload = WebhookPayload.model_validate(raw_json)
    except ValidationError as exc:
        logger.error("Payload validation failed: %s", exc)
        raise HTTPException(status_code=422, detail=exc.errors())

    extra_fields = set(payload.model_extra or {})
    if extra_fields:
        logger.debug(
            "[%s] Unexpected fields in payload: %s", payload.meeting_id, extra_fields
        )

    logger.info(
        "[%s] Webhook received for meeting: %s", payload.meeting_id, payload.title
    )

    result = process_meeting(payload)

    if result.status == ProcessingStatus.DUPLICATE:
        return {"status": "duplicate", "meeting_id": payload.meeting_id}

    if result.status == ProcessingStatus.FAILED:
        raise HTTPException(status_code=500, detail=result.error or "Processing failed")

    return {"status": "processed", "meeting_id": payload.meeting_id}
