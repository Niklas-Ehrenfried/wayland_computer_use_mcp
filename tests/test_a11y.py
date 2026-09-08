"""Tests for AT-SPI2 accessibility tree inspection and pruning."""

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
