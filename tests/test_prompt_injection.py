import pytest


def test_reminder_system_constant_exists():
    from app.tasks.reminder import REMINDER_SYSTEM
    assert isinstance(REMINDER_SYSTEM, str)
    assert len(REMINDER_SYSTEM) > 50


def test_general_system_constant_exists():
    from app.tasks.recall import GENERAL_SYSTEM
    assert isinstance(GENERAL_SYSTEM, str)
    assert len(GENERAL_SYSTEM) > 50


def test_no_body_interpolation_in_reminder_system():
    """System message in reminder.py must NOT contain a {body} f-string slot."""
    import ast
    import pathlib
    source = pathlib.Path("app/tasks/reminder.py").read_text()
    # Check that the REMINDER_SYSTEM constant does not contain body placeholder
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "REMINDER_SYSTEM":
                    # The value should be a string constant, not an f-string containing {body}
                    assert not isinstance(node.value, ast.JoinedStr), \
                        "REMINDER_SYSTEM must not be an f-string"


def test_llm_messages_json_called_in_reminder():
    import inspect
    import pathlib
    source = pathlib.Path("app/tasks/reminder.py").read_text()
    assert "llm_messages_json" in source, "reminder.py must use llm_messages_json"
    assert 'role": "system"' in source or "role: system" in source.lower(), \
        "reminder.py must define a system role message"
    assert 'role": "user"' in source or "role: user" in source.lower(), \
        "reminder.py must define a user role message"


def test_llm_messages_json_called_in_recall():
    import pathlib
    source = pathlib.Path("app/tasks/recall.py").read_text()
    assert "llm_messages_json" in source, "recall.py handle_general must use llm_messages_json"
