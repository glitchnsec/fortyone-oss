import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_llm_messages_json_returns_mock_when_no_key():
    with patch("app.tasks._llm.get_settings") as mock_settings:
        mock_settings.return_value = MagicMock(has_llm=False)
        from app.tasks._llm import llm_messages_json
        result = await llm_messages_json(
            messages=[{"role": "system", "content": "system"}, {"role": "user", "content": "user"}],
            mock_payload={"task": "test", "confirmation": "Got it"},
        )
        assert result == {"task": "test", "confirmation": "Got it"}


@pytest.mark.asyncio
async def test_llm_messages_json_passes_messages_directly():
    with patch("app.tasks._llm.get_settings") as mock_settings:
        mock_settings.return_value = MagicMock(
            has_llm=True,
            llm_model_fast="test-model",
        )
        with patch("app.tasks._llm._client") as mock_client_fn:
            import json as json_lib
            mock_completion = MagicMock()
            mock_completion.choices[0].message.content = json_lib.dumps({"task": "call John"})
            mock_completion.usage.completion_tokens = 10
            mock_client = MagicMock()
            mock_client.chat.completions.create = AsyncMock(return_value=mock_completion)
            mock_client_fn.return_value = mock_client

            from app.tasks._llm import llm_messages_json
            messages = [
                {"role": "system", "content": "You extract reminders."},
                {"role": "user", "content": "Remind me to call John tomorrow"},
            ]
            result = await llm_messages_json(messages=messages, mock_payload={})

            call_kwargs = mock_client.chat.completions.create.call_args[1]
            assert call_kwargs["messages"] == messages


def test_llm_messages_json_is_async():
    import inspect
    from app.tasks._llm import llm_messages_json
    assert inspect.iscoroutinefunction(llm_messages_json)
