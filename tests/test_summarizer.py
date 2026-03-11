import pytest
from unittest.mock import MagicMock, patch

import config
import summarizer as sm


INSTRUCTIONS_CONTENT = "You are a meeting summarizer. Create structured summaries."
SAMPLE_TRANSCRIPT = "Alice: Let's review the sprint. Bob: Completed 8 tickets. Alice: Great work."


@pytest.fixture(autouse=True)
def reset_cache():
    sm._instructions = None
    sm._openai_client = None
    yield
    sm._instructions = None
    sm._openai_client = None


@pytest.fixture
def instructions_file(tmp_path):
    path = tmp_path / "instructions.md"
    path.write_text(INSTRUCTIONS_CONTENT, encoding="utf-8")
    return str(path)


@pytest.fixture(autouse=True)
def patch_instructions_file(instructions_file, monkeypatch):
    monkeypatch.setattr(sm, "INSTRUCTIONS_FILE", instructions_file)


@pytest.fixture
def mock_openai_response():
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = "# Summary\n\nSpring review completed."
    response.usage = MagicMock()
    response.usage.total_tokens = 150
    return response


@pytest.fixture
def mock_openai_client(mock_openai_response):
    client = MagicMock()
    client.chat.completions.create.return_value = mock_openai_response
    return client


class TestSystemMessage:
    def test_system_message_is_instructions_content(self, mock_openai_client):
        with patch.object(sm, "_get_client", return_value=mock_openai_client):
            sm.generate_summary("m-1", "Sprint Review", SAMPLE_TRANSCRIPT)

        call_kwargs = mock_openai_client.chat.completions.create.call_args
        messages = call_kwargs.kwargs["messages"]
        system_msg = next(m for m in messages if m["role"] == "system")
        assert system_msg["content"] == INSTRUCTIONS_CONTENT


class TestUserMessage:
    def test_user_message_starts_with_required_prefix(self, mock_openai_client):
        with patch.object(sm, "_get_client", return_value=mock_openai_client):
            sm.generate_summary("m-1", "Sprint Review", SAMPLE_TRANSCRIPT)

        call_kwargs = mock_openai_client.chat.completions.create.call_args
        messages = call_kwargs.kwargs["messages"]
        user_msg = next(m for m in messages if m["role"] == "user")
        expected_prefix = (
            "generate meeting summary for this as per the instructions without citations\n\n"
        )
        assert user_msg["content"].startswith(expected_prefix)

    def test_user_message_contains_transcript(self, mock_openai_client):
        with patch.object(sm, "_get_client", return_value=mock_openai_client):
            sm.generate_summary("m-1", "Sprint Review", SAMPLE_TRANSCRIPT)

        call_kwargs = mock_openai_client.chat.completions.create.call_args
        messages = call_kwargs.kwargs["messages"]
        user_msg = next(m for m in messages if m["role"] == "user")
        assert SAMPLE_TRANSCRIPT in user_msg["content"]


class TestModelConfig:
    def test_uses_configured_model(self, mock_openai_client, monkeypatch):
        monkeypatch.setattr(config, "OPENAI_MODEL", "gpt-4-turbo")
        with patch.object(sm, "_get_client", return_value=mock_openai_client):
            sm.generate_summary("m-1", "Test", SAMPLE_TRANSCRIPT)

        call_kwargs = mock_openai_client.chat.completions.create.call_args
        assert call_kwargs.kwargs["model"] == "gpt-4-turbo"

    def test_temperature_is_0_3(self, mock_openai_client):
        with patch.object(sm, "_get_client", return_value=mock_openai_client):
            sm.generate_summary("m-1", "Test", SAMPLE_TRANSCRIPT)

        call_kwargs = mock_openai_client.chat.completions.create.call_args
        assert call_kwargs.kwargs["temperature"] == 0.3


class TestTranscriptSizePolicy:
    def test_transcript_under_threshold_passed_unmodified(self, mock_openai_client, monkeypatch):
        monkeypatch.setattr(config, "MAX_TRANSCRIPT_CHARS", 1000)
        short_transcript = "A" * 500

        with patch.object(sm, "_get_client", return_value=mock_openai_client):
            sm.generate_summary("m-1", "Test", short_transcript)

        call_kwargs = mock_openai_client.chat.completions.create.call_args
        messages = call_kwargs.kwargs["messages"]
        user_msg = next(m for m in messages if m["role"] == "user")
        assert short_transcript in user_msg["content"]
        assert "[Transcript truncated" not in user_msg["content"]

    def test_transcript_over_threshold_truncated(self, mock_openai_client, monkeypatch):
        monkeypatch.setattr(config, "MAX_TRANSCRIPT_CHARS", 100)
        long_transcript = "B" * 500

        with patch.object(sm, "_get_client", return_value=mock_openai_client):
            sm.generate_summary("m-1", "Test", long_transcript)

        call_kwargs = mock_openai_client.chat.completions.create.call_args
        messages = call_kwargs.kwargs["messages"]
        user_msg = next(m for m in messages if m["role"] == "user")
        assert "[Transcript truncated due to size limit]" in user_msg["content"]

    def test_truncated_transcript_has_correct_length(self, mock_openai_client, monkeypatch):
        threshold = 100
        monkeypatch.setattr(config, "MAX_TRANSCRIPT_CHARS", threshold)
        long_transcript = "C" * 500

        with patch.object(sm, "_get_client", return_value=mock_openai_client):
            sm.generate_summary("m-1", "Test", long_transcript)

        call_kwargs = mock_openai_client.chat.completions.create.call_args
        messages = call_kwargs.kwargs["messages"]
        user_msg = next(m for m in messages if m["role"] == "user")
        prefix = "generate meeting summary for this as per the instructions without citations\n\n"
        actual_transcript_in_msg = user_msg["content"][len(prefix):]
        assert actual_transcript_in_msg.startswith("C" * threshold)
