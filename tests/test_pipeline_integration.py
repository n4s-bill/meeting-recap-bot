import json
import pytest
import pytest_asyncio
import respx
import httpx
from unittest.mock import MagicMock, patch
from httpx import ASGITransport, AsyncClient

import config
import emailer as em
import storage as st
import summarizer as sm
import meeting_type_config as mtc
from webhook_server import app
from pipeline import ProcessingStatus


VALID_SECRET = "integration-secret"
SENDER_EMAIL = "sender@scribendi.com"
BILL_EMAIL = "bill.johnson@scribendi.com"
GRAPH_SEND_URL = f"https://graph.microsoft.com/v1.0/users/{SENDER_EMAIL}/sendMail"

SAMPLE_PAYLOAD = {
    "meeting_id": "int-test-001",
    "title": "Weekly Engineering Standup",
    "date": "2026-03-10T14:00:00Z",
    "participants": ["alice@scribendi.com", "bob@scribendi.com"],
    "transcript": "Alice: Good morning. Bob: Good morning. Alice: Any blockers? Bob: No blockers.",
}


@pytest.fixture(autouse=True)
def patch_all_config(monkeypatch, storage_file, meeting_types_file, tmp_path):
    monkeypatch.setattr(config, "WEBHOOK_SECRET", VALID_SECRET)
    monkeypatch.setattr(config, "EMAIL_FROM", SENDER_EMAIL)
    monkeypatch.setattr(config, "EMAIL_CC", BILL_EMAIL)
    monkeypatch.setattr(config, "MS_GRAPH_TENANT_ID", "tenant-id")
    monkeypatch.setattr(config, "MS_GRAPH_CLIENT_ID", "client-id")
    monkeypatch.setattr(config, "MS_GRAPH_CLIENT_SECRET", "secret")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-fake")
    monkeypatch.setattr(config, "OPENAI_MODEL", "gpt-4o")

    monkeypatch.setattr(st, "STORAGE_FILE", storage_file)
    monkeypatch.setattr(st, "_LOCK_FILE", storage_file + ".lock")

    monkeypatch.setattr(mtc, "MEETING_TYPES_FILE", meeting_types_file)
    mtc._meeting_types = None

    em._credential = None
    sm._instructions = None
    sm._openai_client = None


@pytest.fixture
def mock_token():
    mock_cred = MagicMock()
    mock_tok = MagicMock()
    mock_tok.token = "fake-token"
    mock_cred.get_token.return_value = mock_tok
    with patch.object(em, "_get_credential", return_value=mock_cred):
        yield


@pytest.fixture
def mock_openai_client():
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = "# Summary\n\nStandup notes here."
    response.usage = MagicMock()
    response.usage.total_tokens = 200
    client = MagicMock()
    client.chat.completions.create.return_value = response
    with patch.object(sm, "_get_client", return_value=client):
        with patch.object(sm, "_get_instructions", return_value="You are a summarizer."):
            yield client


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


def _post_webhook(client, payload=None, secret=VALID_SECRET):
    return client.post(
        "/webhook/transcript",
        json=payload or SAMPLE_PAYLOAD,
        headers={"X-Webhook-Secret": secret},
    )


class TestHappyPath:
    @respx.mock
    @pytest.mark.asyncio
    async def test_full_pipeline_success(self, client, mock_openai_client, mock_token, storage_file):
        route = respx.post(GRAPH_SEND_URL).mock(return_value=httpx.Response(202))
        resp = await _post_webhook(client)

        assert resp.status_code == 200
        assert resp.json()["status"] == "processed"

        assert route.called
        assert st.is_processed("int-test-001")


class TestDuplicateMeeting:
    @respx.mock
    @pytest.mark.asyncio
    async def test_first_call_succeeds(self, client, mock_openai_client, mock_token):
        respx.post(GRAPH_SEND_URL).mock(return_value=httpx.Response(202))
        resp = await _post_webhook(client)
        assert resp.json()["status"] == "processed"

    @respx.mock
    @pytest.mark.asyncio
    async def test_second_call_is_duplicate(self, client, mock_openai_client, mock_token):
        respx.post(GRAPH_SEND_URL).mock(return_value=httpx.Response(202))
        await _post_webhook(client)

        # Second call with same payload -- no new Graph calls needed
        resp = await _post_webhook(client)
        assert resp.status_code == 200
        assert resp.json()["status"] == "duplicate"


class TestMissingParticipants:
    @respx.mock
    @pytest.mark.asyncio
    async def test_falls_through_to_distro_list(self, client, mock_openai_client, mock_token):
        route = respx.post(GRAPH_SEND_URL).mock(return_value=httpx.Response(202))
        payload = {**SAMPLE_PAYLOAD, "participants": [], "meeting_id": "no-participants-001",
                   "title": "Weekly Engineering Standup"}
        resp = await _post_webhook(client, payload)
        assert resp.status_code == 200
        import json as _json
        body = _json.loads(route.calls[0].request.content)
        to_addrs = [r["emailAddress"]["address"] for r in body["message"]["toRecipients"]]
        assert "engineering-team@company.com" in to_addrs

    @respx.mock
    @pytest.mark.asyncio
    async def test_falls_through_to_bill_fallback(self, client, mock_openai_client, mock_token):
        route = respx.post(GRAPH_SEND_URL).mock(return_value=httpx.Response(202))
        payload = {**SAMPLE_PAYLOAD, "participants": [], "meeting_id": "fallback-001",
                   "title": "Ad Hoc Board Conversation"}
        resp = await _post_webhook(client, payload)
        assert resp.status_code == 200
        import json as _json
        body = _json.loads(route.calls[0].request.content)
        to_addrs = [r["emailAddress"]["address"] for r in body["message"]["toRecipients"]]
        assert BILL_EMAIL in to_addrs


class TestInvalidWebhookSecret:
    @pytest.mark.asyncio
    async def test_wrong_secret_no_processing(self, client, mock_openai_client, storage_file):
        resp = await _post_webhook(client, secret="wrong")
        assert resp.status_code == 401
        assert not st.is_processed(SAMPLE_PAYLOAD["meeting_id"])


class TestOpenAIFailure:
    @respx.mock
    @pytest.mark.asyncio
    async def test_openai_failure_sends_notification_and_returns_500(
        self, client, mock_token, storage_file
    ):
        respx.post(GRAPH_SEND_URL).mock(return_value=httpx.Response(202))

        failing_client = MagicMock()
        failing_client.chat.completions.create.side_effect = RuntimeError("OpenAI down")

        with patch.object(sm, "_get_client", return_value=failing_client):
            with patch.object(sm, "_get_instructions", return_value="instructions"):
                resp = await _post_webhook(client)

        assert resp.status_code == 500
        assert not st.is_processed(SAMPLE_PAYLOAD["meeting_id"])

    @respx.mock
    @pytest.mark.asyncio
    async def test_openai_failure_not_marked_processed(self, client, mock_token, storage_file):
        respx.post(GRAPH_SEND_URL).mock(return_value=httpx.Response(202))

        failing_client = MagicMock()
        failing_client.chat.completions.create.side_effect = RuntimeError("Fail")

        with patch.object(sm, "_get_client", return_value=failing_client):
            with patch.object(sm, "_get_instructions", return_value="instructions"):
                await _post_webhook(client)

        assert not st.is_processed(SAMPLE_PAYLOAD["meeting_id"])


class TestGraphFailure:
    @respx.mock
    @pytest.mark.asyncio
    async def test_graph_failure_returns_500(self, client, mock_openai_client, mock_token, storage_file):
        respx.post(GRAPH_SEND_URL).mock(return_value=httpx.Response(503))
        resp = await _post_webhook(client)
        assert resp.status_code == 500

    @respx.mock
    @pytest.mark.asyncio
    async def test_graph_failure_not_marked_processed(
        self, client, mock_openai_client, mock_token, storage_file
    ):
        respx.post(GRAPH_SEND_URL).mock(return_value=httpx.Response(503))
        await _post_webhook(client)
        assert not st.is_processed(SAMPLE_PAYLOAD["meeting_id"])


class TestOversizedTranscript:
    @respx.mock
    @pytest.mark.asyncio
    async def test_oversized_transcript_is_processed_normally(
        self, client, mock_openai_client, mock_token, monkeypatch
    ):
        route = respx.post(GRAPH_SEND_URL).mock(return_value=httpx.Response(202))
        monkeypatch.setattr(config, "MAX_TRANSCRIPT_CHARS", 50)

        payload = {**SAMPLE_PAYLOAD, "meeting_id": "big-transcript-001",
                   "transcript": "Word " * 1000}
        resp = await _post_webhook(client, payload)
        assert resp.status_code == 200

        call_kwargs = mock_openai_client.chat.completions.create.call_args
        messages = call_kwargs.kwargs["messages"]
        user_msg = next(m for m in messages if m["role"] == "user")
        assert "[Transcript truncated due to size limit]" in user_msg["content"]
