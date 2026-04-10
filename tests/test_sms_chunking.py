from app.channels.sms import _MAX_SMS_CHARS, _split_sms_parts


def test_split_sms_parts_keeps_short_message_single_part():
    body = "Hello from Jarvis."
    parts = _split_sms_parts(body)
    assert parts == [body]


def test_split_sms_parts_splits_long_message_with_prefix():
    body = ("AI security best practices. " * 120).strip()
    parts = _split_sms_parts(body)

    assert len(parts) > 1
    assert parts[0].startswith("(1/")
    assert all(len(p) <= _MAX_SMS_CHARS for p in parts)


def test_split_sms_parts_handles_single_long_token():
    body = "x" * (_MAX_SMS_CHARS + 250)
    parts = _split_sms_parts(body)

    assert len(parts) >= 2
    assert all(len(p) <= _MAX_SMS_CHARS for p in parts)
