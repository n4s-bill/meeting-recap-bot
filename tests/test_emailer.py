import pytest
import respx
import httpx
from unittest.mock import MagicMock, patch

import config
import emailer as em

EMAIL_FROM = "sender@scribendi.com"
EMAIL_CC_ADDR = "bill.johnson@scribendi.com"
GRAPH_SEND_URL = f"https://graph.microsoft.com/v1.0/users/{EMAIL_FROM}/sendMail"

SAMPLE_MARKDOWN = "# Sprint Review\n\n- Completed 8 tickets\n- No blockers\n"


@pytest.fixture(autouse=True)
def patch_config(monkeypatch):
    monkeypatch.setattr(config, "EMAIL_FROM", EMAIL_FROM)
    monkeypatch.setattr(config, "EMAIL_CC", EMAIL_CC_ADDR)
    monkeypatch.setattr(config, "MS_GRAPH_TENANT_ID", "tenant-123")
    monkeypatch.setattr(config, "MS_GRAPH_CLIENT_ID", "client-123")
    monkeypatch.setattr(config, "MS_GRAPH_CLIENT_SECRET", "secret-xyz")
    em._credential = None


@pytest.fixture
def mock_token():
    mock_cred = MagicMock()
    mock_token = MagicMock()
    mock_token.token = "fake-bearer-token"
    mock_cred.get_token.return_value = mock_token
    with patch.object(em, "_get_credential", return_value=mock_cred):
        yield


class TestSubjectFormat:
    @respx.mock
    def test_subject_uses_em_dash(self, mock_token):
        route = respx.post(GRAPH_SEND_URL).mock(return_value=httpx.Response(202))
        em.send_recap("m-1", "Sprint Review", "2026-03-10T14:00:00Z", ["alice@co.com"], [EMAIL_CC_ADDR], SAMPLE_MARKDOWN)
        payload = route.calls[0].request
        import json
        body = json.loads(payload.content)
        assert "\u2014" in body["message"]["subject"]

    @respx.mock
    def test_subject_format(self, mock_token):
        route = respx.post(GRAPH_SEND_URL).mock(return_value=httpx.Response(202))
        em.send_recap("m-1", "Sprint Review", "2026-03-10T14:00:00Z", ["alice@co.com"], [EMAIL_CC_ADDR], SAMPLE_MARKDOWN)
        import json
        body = json.loads(route.calls[0].request.content)
        assert body["message"]["subject"] == "[\u200bMeeting Recap] Sprint Review \u2014 Mar 10, 2026".replace("\u200b", "")

    @respx.mock
    def test_subject_meeting_recap_prefix(self, mock_token):
        route = respx.post(GRAPH_SEND_URL).mock(return_value=httpx.Response(202))
        em.send_recap("m-1", "My Meeting", "2026-01-15T00:00:00Z", ["a@b.com"], [], SAMPLE_MARKDOWN)
        import json
        body = json.loads(route.calls[0].request.content)
        assert body["message"]["subject"].startswith("[Meeting Recap]")


class TestRecipients:
    @respx.mock
    def test_to_recipients_match_resolved_list(self, mock_token):
        route = respx.post(GRAPH_SEND_URL).mock(return_value=httpx.Response(202))
        em.send_recap("m-1", "T", "2026-01-01T00:00:00Z", ["alice@co.com", "bob@co.com"], [EMAIL_CC_ADDR], SAMPLE_MARKDOWN)
        import json
        body = json.loads(route.calls[0].request.content)
        to_addrs = [r["emailAddress"]["address"] for r in body["message"]["toRecipients"]]
        assert to_addrs == ["alice@co.com", "bob@co.com"]

    @respx.mock
    def test_cc_contains_bill(self, mock_token):
        route = respx.post(GRAPH_SEND_URL).mock(return_value=httpx.Response(202))
        em.send_recap("m-1", "T", "2026-01-01T00:00:00Z", ["alice@co.com"], [EMAIL_CC_ADDR], SAMPLE_MARKDOWN)
        import json
        body = json.loads(route.calls[0].request.content)
        cc_addrs = [r["emailAddress"]["address"] for r in body["message"]["ccRecipients"]]
        assert EMAIL_CC_ADDR in cc_addrs


class TestHTMLBody:
    @respx.mock
    def test_body_content_type_is_html(self, mock_token):
        route = respx.post(GRAPH_SEND_URL).mock(return_value=httpx.Response(202))
        em.send_recap("m-1", "T", "2026-01-01T00:00:00Z", ["a@b.com"], [], SAMPLE_MARKDOWN)
        import json
        body = json.loads(route.calls[0].request.content)
        assert body["message"]["body"]["contentType"] == "HTML"

    @respx.mock
    def test_markdown_converted_to_html(self, mock_token):
        route = respx.post(GRAPH_SEND_URL).mock(return_value=httpx.Response(202))
        em.send_recap("m-1", "T", "2026-01-01T00:00:00Z", ["a@b.com"], [], "# Heading\n\nParagraph.")
        import json
        body = json.loads(route.calls[0].request.content)
        html = body["message"]["body"]["content"]
        assert "<h1>" in html
        assert "<p>" in html

    def test_html_sanitization_removes_script_tags(self):
        dangerous = "<p>Safe</p><script>alert('xss')</script>"
        result = em._markdown_to_safe_html(dangerous)
        assert "<script>" not in result
        assert "Safe" in result

    def test_html_sanitization_removes_event_handlers(self):
        dangerous = '<p onclick="evil()">Click</p>'
        result = em._markdown_to_safe_html(dangerous)
        assert "onclick" not in result

    def test_html_sanitization_allows_safe_tags(self):
        md = "# H1\n\n**bold** and *em* and `code`"
        result = em._markdown_to_safe_html(md)
        assert "<h1>" in result
        assert "<strong>" in result
        assert "<em>" in result
        assert "<code>" in result


class TestGraphRestCall:
    @respx.mock
    def test_posts_to_correct_url(self, mock_token):
        route = respx.post(GRAPH_SEND_URL).mock(return_value=httpx.Response(202))
        em.send_recap("m-1", "T", "2026-01-01T00:00:00Z", ["a@b.com"], [], SAMPLE_MARKDOWN)
        assert route.called

    @respx.mock
    def test_authorization_header_set(self, mock_token):
        route = respx.post(GRAPH_SEND_URL).mock(return_value=httpx.Response(202))
        em.send_recap("m-1", "T", "2026-01-01T00:00:00Z", ["a@b.com"], [], SAMPLE_MARKDOWN)
        auth_header = route.calls[0].request.headers.get("authorization")
        assert auth_header == "Bearer fake-bearer-token"

    @respx.mock
    def test_save_to_sent_items_true(self, mock_token):
        route = respx.post(GRAPH_SEND_URL).mock(return_value=httpx.Response(202))
        em.send_recap("m-1", "T", "2026-01-01T00:00:00Z", ["a@b.com"], [], SAMPLE_MARKDOWN)
        import json
        body = json.loads(route.calls[0].request.content)
        assert body["saveToSentItems"] is True


class TestFailureNotification:
    @respx.mock
    def test_failure_notification_subject(self, mock_token):
        route = respx.post(GRAPH_SEND_URL).mock(return_value=httpx.Response(202))
        em.send_failure_notification("m-1", "Sprint Review", "2026-03-10T14:00:00Z", "API error")
        import json
        body = json.loads(route.calls[0].request.content)
        assert "[Meeting Recap - FAILED]" in body["message"]["subject"]

    @respx.mock
    def test_failure_notification_sent_to_bill(self, mock_token):
        route = respx.post(GRAPH_SEND_URL).mock(return_value=httpx.Response(202))
        em.send_failure_notification("m-1", "T", "2026-01-01T00:00:00Z", "error")
        import json
        body = json.loads(route.calls[0].request.content)
        to_addrs = [r["emailAddress"]["address"] for r in body["message"]["toRecipients"]]
        assert EMAIL_CC_ADDR in to_addrs

    @respx.mock
    def test_failure_notification_body_contains_error(self, mock_token):
        route = respx.post(GRAPH_SEND_URL).mock(return_value=httpx.Response(202))
        em.send_failure_notification("m-1", "T", "2026-01-01T00:00:00Z", "RateLimitError occurred")
        import json
        body = json.loads(route.calls[0].request.content)
        assert "RateLimitError occurred" in body["message"]["body"]["content"]
