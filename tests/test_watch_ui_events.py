"""Unit tests for watch_ui_events, AtspiEventListener, and granular batch deltas."""

from unittest.mock import MagicMock

import pytest

from wayland_computer_use_mcp.a11y.events import AtspiEventListener
from wayland_computer_use_mcp.config import get_config
from wayland_computer_use_mcp.server import (
    batch_actions,
    list_managed_apps,
    watch_ui_events,
)


@pytest.fixture(autouse=True)
def setup_mock(monkeypatch):
    monkeypatch.setattr(get_config(), "mock_mode", True)


def test_event_listener_init_and_buffering():
    """Verifies that AtspiEventListener buffers signals correctly."""
    listener = AtspiEventListener()
    assert listener.get_recent_mutations() == []

    # Simulate incoming signal message
    mock_msg = MagicMock()
    mock_msg.message_type.name = "SIGNAL"
    # match MessageType.SIGNAL value
    from dbus_fast import MessageType

    mock_msg.message_type = MessageType.SIGNAL
    mock_msg.interface = "org.a11y.atspi.Event.Object"
    mock_msg.member = "StateChanged"
    mock_msg.path = "/org/a11y/atspi/accessible/123"
    mock_msg.body = ["focused", True]

    listener._message_handler(mock_msg)
    mutations = listener.get_recent_mutations()
    assert len(mutations) == 1
    assert mutations[0]["action"] == "StateChanged"
    assert mutations[0]["detail"] == "focused"


def test_wait_for_settled_mock():
    """Verifies wait_for_settled returns settled in mock mode."""
    listener = AtspiEventListener()
    res = listener.wait_for_settled(timeout_ms=100)
    assert res["settled"] is True
    assert "mutations" in res


def test_watch_ui_events_tool():
    """Verifies watch_ui_events tool execution and return structure."""
    res = watch_ui_events(timeout_ms=100)
    assert res["settled"] is True
    assert "mutations" in res
    assert "elapsed_ms" in res


def test_list_managed_apps_tool():
    """Verifies list_managed_apps tool execution."""
    res = list_managed_apps()
    assert "active_apps_count" in res
    assert "apps" in res


def test_batch_actions_with_per_step_delta(monkeypatch):
    """Verifies batch_actions executes steps and reports status cleanly."""
    from wayland_computer_use_mcp.portal import global_portal_session

    monkeypatch.setattr(global_portal_session, "_is_mock", True)
    monkeypatch.setattr(
        global_portal_session, "dispatch_hover", lambda *args, **kwargs: "Hovered"
    )
    batch = [
        {"action": "wait", "ms": 50},
        {"action": "hover", "x": 10, "y": 10},
    ]
    res = batch_actions(batch, pid=99999)
    assert res["status"] == "success", f"Result: {res}"
    assert res["executed_steps_count"] == 2
    assert len(res["results"]) == 2
    assert "Waited 50ms" in res["results"][0]



