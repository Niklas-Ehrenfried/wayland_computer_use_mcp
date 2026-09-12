"""Pytest fixtures and session isolation for wayland-computer-use-mcp."""

import pytest

from wayland_computer_use_mcp.a11y import clear_node_cache
from wayland_computer_use_mcp.portal import global_portal_session
from wayland_computer_use_mcp.process import is_responsive, terminate


@pytest.fixture(autouse=True)
def isolate_session_state(request):
    """Isolates portal session state and enforces mock containment for non-live tests."""
    from wayland_computer_use_mcp.config import get_config

    is_live = request.node.get_closest_marker("live") is not None
    if not is_live:
        clear_node_cache()
        global_portal_session.target_pid = None
        get_config().mock_mode = True
        global_portal_session._is_mock = True

        yield

        # Cleanup target process if still active
        current_pid = global_portal_session.target_pid
        if current_pid and is_responsive(current_pid):
            try:
                terminate(current_pid)
            except Exception:
                pass
        global_portal_session.target_pid = None
        clear_node_cache()
    else:
        # For live desktop tests, allow the live fixture to manage process lifecycle
        yield
