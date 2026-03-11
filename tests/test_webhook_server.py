import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from unittest.mock import patch, MagicMock

import config
from webhook_server import app
from pipeline import ProcessingResult, ProcessingStatus


VALID_SECRET = "test-secret-xyz"
SAMPLE_PAYLOAD = {
    "meeting_id": "test-meeting-001",
    "title": "Weekly Engineering Standup",
    "date": "2026-03-10T14:00:00Z",
    "participants": [
        {"name": "Alice", "email": "alice@scribendi.com", "permission": "..."},
        {"name": "Bob", "email": "bob@scribendi.com", "permission": "..."},
    ],
    "transcript": "Alice: Good morning. Bob: Good morning.",
}


@pytest.fixture(autouse=True)
def patch_webhook_secret(monkeypatch):
    monkeypatch.setattr(config, "WEBHOOK_SECRET", VALID_SECRET)


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


@pytest.fixture
def mock_pipeline_success():
    with patch("webhook_server.process_meeting") as mock:
        mock.return_value = ProcessingResult(status=ProcessingStatus.SUCCESS)
        yield mock


@pytest.fixture
def mock_pipeline_duplicate():
    with patch("webhook_server.process_meeting") as mock:
        mock.return_value = ProcessingResult(status=ProcessingStatus.DUPLICATE)
        yield mock


@pytest.fixture
def mock_pipeline_failed():
    with patch("webhook_server.process_meeting") as mock:
        mock.return_value = ProcessingResult(
            status=ProcessingStatus.FAILED, error="OpenAI failed"
        )
        yield mock


class TestHealth:
    @pytest.mark.asyncio
    async def test_health_returns_200(self, client):
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "healthy"}


class TestWebhookAuth:
    @pytest.mark.asyncio
    async def test_valid_x_webhook_secret_returns_200(self, client, mock_pipeline_success):
        resp = await client.post(
            "/webhook/transcript",
            json=SAMPLE_PAYLOAD,
            headers={"X-Webhook-Secret": VALID_SECRET},
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_valid_bearer_token_returns_200(self, client, mock_pipeline_success):
        resp = await client.post(
            "/webhook/transcript",
            json=SAMPLE_PAYLOAD,
            headers={"Authorization": f"Bearer {VALID_SECRET}"},
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_missing_secret_returns_401(self, client):
        resp = await client.post("/webhook/transcript", json=SAMPLE_PAYLOAD)
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_wrong_secret_returns_401(self, client):
        resp = await client.post(
            "/webhook/transcript",
            json=SAMPLE_PAYLOAD,
            headers={"X-Webhook-Secret": "wrong-secret"},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_wrong_bearer_token_returns_401(self, client):
        resp = await client.post(
            "/webhook/transcript",
            json=SAMPLE_PAYLOAD,
            headers={"Authorization": "Bearer wrong-secret"},
        )
        assert resp.status_code == 401


class TestPayloadParsing:
    @pytest.mark.asyncio
    async def test_malformed_json_returns_422(self, client):
        resp = await client.post(
            "/webhook/transcript",
            content=b"NOT JSON {{{",
            headers={
                "X-Webhook-Secret": VALID_SECRET,
                "Content-Type": "application/json",
            },
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_missing_required_fields_returns_422(self, client):
        resp = await client.post(
            "/webhook/transcript",
            json={"title": "A meeting"},
            headers={"X-Webhook-Secret": VALID_SECRET},
        )
        assert resp.status_code == 422


class TestResponseBodies:
    @pytest.mark.asyncio
    async def test_success_response(self, client, mock_pipeline_success):
        resp = await client.post(
            "/webhook/transcript",
            json=SAMPLE_PAYLOAD,
            headers={"X-Webhook-Secret": VALID_SECRET},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "processed"
        assert data["meeting_id"] == SAMPLE_PAYLOAD["meeting_id"]

    @pytest.mark.asyncio
    async def test_duplicate_response(self, client, mock_pipeline_duplicate):
        resp = await client.post(
            "/webhook/transcript",
            json=SAMPLE_PAYLOAD,
            headers={"X-Webhook-Secret": VALID_SECRET},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "duplicate"
        assert data["meeting_id"] == SAMPLE_PAYLOAD["meeting_id"]

    @pytest.mark.asyncio
    async def test_pipeline_failure_returns_500(self, client, mock_pipeline_failed):
        resp = await client.post(
            "/webhook/transcript",
            json=SAMPLE_PAYLOAD,
            headers={"X-Webhook-Secret": VALID_SECRET},
        )
        assert resp.status_code == 500
