from app.tasks.manager import _SMS_RESPONSE_CHAR_TARGET, _enforce_channel_response_budget


def test_enforce_budget_no_change_for_non_sms():
    text = "x" * (_SMS_RESPONSE_CHAR_TARGET + 100)
    out = _enforce_channel_response_budget(text, "slack")
    assert out == text


def test_enforce_budget_trims_long_sms_and_adds_continue_hint():
    text = ("AI security best practices include threat modeling and key management. " * 60).strip()
    out = _enforce_channel_response_budget(text, "sms")

    assert len(out) < len(text)
    assert "Reply 'continue' if you want the rest." in out


def test_enforce_budget_keeps_short_sms_unchanged():
    text = "Here are 3 top AI security practices: least privilege, secret hygiene, and audit logs."
    out = _enforce_channel_response_budget(text, "sms")
    assert out == text
