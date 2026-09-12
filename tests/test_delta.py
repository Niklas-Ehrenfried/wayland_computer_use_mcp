"""Unit tests for semantic UI delta tracking and formatting."""

from wayland_computer_use_mcp.delta import (
    compute_ui_delta,
    format_delta_markdown,
    snapshot_interactive_state,
    wrap_with_delta,
)


def test_compute_ui_delta_no_changes():
    """Verifies empty delta when state is identical."""
    before = {
        "b1": {"id": "b1", "role": "button", "name": "Save", "value": None, "states": {"enabled"}}
    }
    after = {
        "b1": {"id": "b1", "role": "button", "name": "Save", "value": None, "states": {"enabled"}}
    }
    delta = compute_ui_delta(before, after)
    assert not delta["modified"]
    assert not delta["appeared"]
    assert not delta["hidden"]

    md = format_delta_markdown(delta)
    assert "None (no visible state or text change detected)" in md


def test_compute_ui_delta_mutations():
    """Verifies detection of text, value, and state mutations."""
    before = {
        "b1": {
            "id": "b1",
            "role": "button",
            "name": "Click Me (0)",
            "value": 0,
            "states": {"enabled"},
        },
        "e1": {"id": "e1", "role": "entry", "name": "Input", "value": "old", "states": set()},
        "old_node": {"id": "old_node", "role": "panel", "name": "Old"},
    }
    after = {
        "b1": {
            "id": "b1",
            "role": "button",
            "name": "Click Me (1)",
            "value": 1,
            "states": {"enabled", "focused"},
        },
        "e1": {"id": "e1", "role": "entry", "name": "Input", "value": "new", "states": set()},
        "new_node": {"id": "new_node", "role": "panel", "name": "New"},
    }

    delta = compute_ui_delta(before, after)
    assert len(delta["modified"]) == 2
    assert len(delta["appeared"]) == 1
    assert len(delta["hidden"]) == 1

    md = format_delta_markdown(delta)
    assert "• b1 [button]: text 'Click Me (0)' ➔ 'Click Me (1)'" in md
    assert "value 0 ➔ 1" in md
    assert "+focused" in md
    assert "• Appeared: new_node ('New')" in md
    assert "• Hidden: old_node ('Old')" in md


def test_format_delta_markdown_overflow():
    """Verifies truncation formatting when > 5 elements appear or disappear."""
    appeared = [{"id": f"n{i}", "name": f"Node{i}"} for i in range(10)]
    hidden = [{"id": f"h{i}", "name": f"Hidden{i}"} for i in range(8)]
    delta = {"modified": [], "appeared": appeared, "hidden": hidden}

    md = format_delta_markdown(delta)
    assert "+5 more" in md
    assert "+3 more" in md


def test_wrap_with_delta_pid_zero():
    """Verifies wrap_with_delta does not execute snapshot logic if pid <= 0."""
    res = wrap_with_delta(pid=0, action_fn=lambda: "Direct result")
    assert res == "Direct result"


def test_snapshot_interactive_state_error(monkeypatch):
    """Verifies snapshot handles exceptions gracefully."""
    monkeypatch.setattr(
        "wayland_computer_use_mcp.a11y.get_application_tree",
        lambda pid: (_ for _ in ()).throw(RuntimeError("D-Bus fault")),
    )
    snap = snapshot_interactive_state(1234)
    assert snap == {}
