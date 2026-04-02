"""
Tests for Task 1: LLM config defaults and Message model channel/persona_tag columns.

RED phase: these tests should fail before the implementation is updated.
GREEN phase: tests pass after config.py and models.py are updated.
"""
import pytest


def test_llm_model_fast_default():
    """Settings class-level default for llm_model_fast must be openai/gpt-4o-mini (D-13).

    We inspect the model field default rather than a loaded instance because the
    .env file in the working tree may override env vars — that's correct behavior.
    The source-of-truth is the class default, which controls what runs in any
    environment where LLM_MODEL_FAST is not explicitly set.
    """
    from app.config import Settings
    field_default = Settings.model_fields["llm_model_fast"].default
    assert field_default == "openai/gpt-4o-mini", (
        f"Expected class default 'openai/gpt-4o-mini', got '{field_default}'"
    )


def test_llm_model_capable_default():
    """Settings class-level default for llm_model_capable must be anthropic/claude-3.5-haiku (D-13)."""
    from app.config import Settings
    field_default = Settings.model_fields["llm_model_capable"].default
    assert field_default == "anthropic/claude-3.5-haiku", (
        f"Expected class default 'anthropic/claude-3.5-haiku', got '{field_default}'"
    )


def test_message_has_channel_attribute():
    """Message model must expose a `channel` SQLAlchemy column attribute."""
    from app.memory.models import Message
    assert hasattr(Message, "channel"), "Message model missing `channel` attribute"


def test_message_has_persona_tag_attribute():
    """Message model must expose a `persona_tag` SQLAlchemy column attribute."""
    from app.memory.models import Message
    assert hasattr(Message, "persona_tag"), "Message model missing `persona_tag` attribute"
