"""Tests for desktop integration, worktree detection, and icon badging."""

from pathlib import Path

from PIL import Image

from wayland_computer_use_mcp.installer import (
    badge_icon,
    detect_worktree,
    install_desktop_app,
    remove_desktop_app,
)


def test_detect_worktree_fallback_naming(tmp_path):
    # Test directory structure starting with .worktree
    wt_dir = tmp_path / ".worktree-feature-x"
    wt_dir.mkdir()
    script = wt_dir / "main.py"
    script.touch()

    is_wt, identifier = detect_worktree(script)
    assert is_wt is True
    assert identifier == ".worktree-feature-x"


def test_badge_icon_generation(tmp_path):
    dest_icon = tmp_path / "test_icon.png"
    badge_icon(src_icon_path=None, dest_icon_path=dest_icon, badge_text="DEV")

    assert dest_icon.is_file()
    with Image.open(dest_icon) as img:
        assert img.size == (256, 256)
        assert img.mode == "RGBA"


def test_desktop_app_install_and_remove(tmp_path, monkeypatch):
    # Mock applications and icons directories
    mock_apps_dir = tmp_path / "applications"
    mock_icons_dir = tmp_path / "icons"
    monkeypatch.setattr("wayland_computer_use_mcp.installer.APPLICATIONS_DIR", mock_apps_dir)
    monkeypatch.setattr("wayland_computer_use_mcp.installer.ICONS_DIR", mock_icons_dir)

    script = tmp_path / "sample_app.py"
    script.touch()

    res = install_desktop_app(
        app_id="sample-app",
        name="Sample App",
        exec_path=str(script),
        version="v1.0",
        is_dev=True,
    )

    assert res["status"] == "installed"
    assert res["app_id"] == "sample-app-dev"
    assert Path(res["desktop_file"]).is_file()
    assert Path(res["icon_file"]).is_file()

    # Verify .desktop contents
    desktop_content = Path(res["desktop_file"]).read_text()
    assert "Name=Sample App (Dev)" in desktop_content
    assert "sample-app-dev.png" in desktop_content

    # Test removal
    del_res = remove_desktop_app("sample-app")
    assert del_res["status"] == "uninstalled"
    assert not Path(res["desktop_file"]).exists()
    assert not Path(res["icon_file"]).exists()


def test_detect_worktree_git_command(monkeypatch, tmp_path):
    """Verifies worktree detection via git rev-parse command."""
    script = tmp_path / "worktree_app.py"
    script.touch()

    class MockProcess:
        returncode = 0
        stdout = "/path/to/repo/.git/worktrees/feature-branch\n"

    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: MockProcess())

    is_wt, branch = detect_worktree(script)
    assert is_wt is True
    assert branch == "feature-branch"


def test_badge_icon_corrupt_file_fallback(tmp_path):
    """Verifies fallback when source icon file is corrupt or invalid."""
    corrupt_icon = tmp_path / "corrupt.png"
    corrupt_icon.write_bytes(b"not an image file")
    dest_icon = tmp_path / "fallback_icon.png"

    badge_icon(corrupt_icon, dest_icon, "FALL")
    assert dest_icon.is_file()
    with Image.open(dest_icon) as img:
        assert img.size == (256, 256)


def test_install_non_dev_with_custom_icon(tmp_path, monkeypatch):
    """Verifies standard (non-dev) installation preserves raw icon and names."""
    mock_apps = tmp_path / "applications"
    mock_icons = tmp_path / "icons"
    monkeypatch.setattr("wayland_computer_use_mcp.installer.APPLICATIONS_DIR", mock_apps)
    monkeypatch.setattr("wayland_computer_use_mcp.installer.ICONS_DIR", mock_icons)

    script = tmp_path / "prod_app.py"
    script.touch()
    src_icon = tmp_path / "source.png"
    Image.new("RGBA", (128, 128), (255, 100, 0, 255)).save(src_icon)

    from wayland_computer_use_mcp.server import install_to_desktop, uninstall_from_desktop

    res = install_to_desktop(
        app_id="prod-app",
        name="Production App",
        exec_path=str(script),
        icon_path=str(src_icon),
        is_dev=False,
    )
    assert res["status"] == "installed"
    assert res["app_id"] == "prod-app"
    assert Path(res["icon_file"]).exists()

    # Uninstall via server tool
    del_res = uninstall_from_desktop("prod-app")
    assert del_res["status"] == "uninstalled"
