import pytest

from wayland_computer_use_mcp.a11y import AtspiInspector, get_application_tree


def test_tree_pruning_invisible_and_separators():
    inspector = AtspiInspector()

    raw_tree = {
        "role": "application",
        "name": "TestApp",
        "states": ["visible", "enabled"],
        "bounds": [0, 0, 800, 600],
        "children": [
            {
                # Invisible node - should be pruned
                "role": "push button",
                "name": "Hidden Button",
                "states": ["enabled"],  # lacks 'visible' and 'showing'
                "bounds": [10, 10, 50, 20],
                "children": [],
            },
            {
                # Separator node - should be pruned
                "role": "separator",
                "name": "",
                "states": ["visible"],
                "bounds": [0, 30, 800, 2],
                "children": [],
            },
            {
                # Empty anonymous container - should be pruned
                "role": "panel",
                "name": "",
                "states": ["visible"],
                "bounds": [0, 40, 100, 100],
                "children": [],
            },
            {
                # Valid interactive child - should be kept
                "role": "push button",
                "name": "OK",
                "states": ["visible", "enabled", "focused"],
                "bounds": [50, 50, 100, 40],
                "children": [],
            },
        ],
    }

    pruned = inspector._prune_tree(raw_tree)
    assert pruned is not None
    children = pruned["children"]
    assert len(children) == 1
    assert children[0]["name"] == "OK"
    assert children[0]["role"] == "push button"


def test_synthetic_tree_fallback():
    tree = get_application_tree(pid=12345, max_depth=3)
    assert isinstance(tree, dict)
    assert tree["role"] == "application"
    assert "PID 12345" in tree["name"] or "App-12345" in tree["name"]
    assert len(tree["children"]) > 0


@pytest.mark.asyncio
async def test_perform_accessible_action_mocked():
    from unittest.mock import AsyncMock, MagicMock, patch

    from wayland_computer_use_mcp.a11y.actions import (
        do_accessible_action,
        do_accessible_set_text,
        perform_accessible_action,
        perform_accessible_set_text,
    )

    mock_bus = AsyncMock()
    mock_reply = MagicMock(body=[True])
    mock_bus.call.return_value = mock_reply
    mock_bus.disconnect = MagicMock()

    with patch("wayland_computer_use_mcp.a11y.actions.AtspiInspector") as mock_insp_cls:
        insp = mock_insp_cls.return_value
        insp.connect = AsyncMock(return_value=True)
        insp.close = AsyncMock()
        insp.bus = mock_bus

        # Test DoAction
        assert await perform_accessible_action(":1.10", "/node/1", 0) is True
        mock_bus.call.assert_called()

        # Test SetTextContents
        assert await perform_accessible_set_text(":1.10", "/node/1", "New Text") is True

        # Test sync wrappers
        assert do_accessible_action(":1.10", "/node/1", 0) is True
        assert do_accessible_set_text(":1.10", "/node/1", "New Text") is True


@pytest.mark.asyncio
async def test_perform_accessible_text_actions():
    from unittest.mock import AsyncMock, MagicMock, patch

    from wayland_computer_use_mcp.a11y.actions import (
        do_accessible_text_action,
        perform_accessible_text_action,
    )

    mock_bus = AsyncMock()
    mock_reply = MagicMock(body=[True])
    mock_bus.call.return_value = mock_reply
    mock_bus.disconnect = MagicMock()

    with patch("wayland_computer_use_mcp.a11y.actions.AtspiInspector") as mock_insp_cls:
        insp = mock_insp_cls.return_value
        insp.connect = AsyncMock(return_value=True)
        insp.close = AsyncMock()
        insp.bus = mock_bus

        for act in ("copy", "cut", "paste", "select"):
            assert await perform_accessible_text_action(":1.10", "/node/1", act, 0, 5) is True

        assert do_accessible_text_action(":1.10", "/node/1", "copy") is True


def test_accessible_action_failures():
    from wayland_computer_use_mcp.a11y.actions import (
        do_accessible_action,
        do_accessible_set_text,
        do_accessible_text_action,
    )

    # When bus is unavailable, sync methods cleanly return False
    assert do_accessible_action("invalid.bus", "/invalid", 0) is False
    assert do_accessible_set_text("invalid.bus", "/invalid", "test") is False
    assert do_accessible_text_action("invalid.bus", "/invalid", "unknown") is False
