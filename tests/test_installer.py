"""Tests for desktop integration, worktree detection, icon badging, and universal targets."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from PIL import Image

from wayland_computer_use_mcp.installer import (
    badge_icon,
    detect_worktree,
    install_desktop_app,
    remove_desktop_app,
)
from wayland_computer_use_mcp.server import install_to_desktop, uninstall_from_desktop


def test_detect_worktree_fallback_and_git_command(tmp_path, monkeypatch):
    wt_dir = tmp_path / ".worktree-feature-x"
    wt_dir.mkdir()
    script = wt_dir / "main.py"
    script.touch()
    assert detect_worktree(script) == (True, ".worktree-feature-x")

    wt_script = tmp_path / "worktree_app.py"
    wt_script.touch()
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: MagicMock(
            returncode=0, stdout="/path/to/repo/.git/worktrees/feature-branch\n"
        ),
    )
    assert detect_worktree(wt_script) == (True, "feature-branch")


def test_badge_icon_generation_and_corrupt_fallback(tmp_path):
    dest_icon = tmp_path / "test_icon.png"
    badge_icon(src_icon_path=None, dest_icon_path=dest_icon, badge_text="DEV")
    assert dest_icon.is_file()
    with Image.open(dest_icon) as img:
        assert img.size == (256, 256) and img.mode == "RGBA"

    corrupt_icon = tmp_path / "corrupt.png"
    corrupt_icon.write_bytes(b"not an image file")
    fallback_icon = tmp_path / "fallback_icon.png"
    badge_icon(corrupt_icon, fallback_icon, "FALL")
    assert fallback_icon.is_file()
    with Image.open(fallback_icon) as img:
        assert img.size == (256, 256)


def test_desktop_app_install_remove_and_universal_targets(tmp_path, monkeypatch):
    mock_apps_dir = tmp_path / "applications"
    mock_icons_dir = tmp_path / "icons"
    monkeypatch.setattr("wayland_computer_use_mcp.installer.APPLICATIONS_DIR", mock_apps_dir)
    monkeypatch.setattr("wayland_computer_use_mcp.installer.ICONS_DIR", mock_icons_dir)
    monkeypatch.setattr(
        "wayland_computer_use_mcp.installer.subprocess.run",
        lambda *a, **kw: MagicMock(returncode=0),
    )

    script = tmp_path / "sample_app.py"
    script.touch()
    res = install_desktop_app(
        app_id="sample-app", name="Sample App", exec_path=str(script), version="v1.0", is_dev=True
    )
    assert res["status"] == "installed" and res["app_id"] == "sample-app-dev"
    desktop_content = Path(res["desktop_file"]).read_text()
    assert "Name=Sample App (Dev)" in desktop_content and "sample-app-dev.png" in desktop_content
    assert remove_desktop_app("sample-app")["status"] == "uninstalled"
    assert not Path(res["desktop_file"]).exists()

    # Production install via server tools with custom icon
    prod_script = tmp_path / "prod_app.py"
    prod_script.touch()
    src_icon = tmp_path / "source.png"
    Image.new("RGBA", (128, 128), (255, 100, 0, 255)).save(src_icon)
    prod_res = install_to_desktop(
        app_id="prod-app",
        name="Production App",
        exec_path=str(prod_script),
        icon_path=str(src_icon),
        is_dev=False,
    )
    assert prod_res["status"] == "installed" and prod_res["app_id"] == "prod-app"
    assert uninstall_from_desktop("prod-app")["status"] == "uninstalled"

    # Shell script, URL, and Flatpak targets
    sh_script = tmp_path / "launch_studio.sh"
    sh_script.write_text("#!/bin/bash\necho hi\n")
    res_sh = install_desktop_app(
        app_id="my-studio",
        name="My Studio",
        exec_path=str(sh_script),
        args=["--fullscreen"],
        terminal=True,
        categories="Utility;Development;",
    )
    entry_sh = (mock_apps_dir / "my-studio.desktop").read_text()
    assert (
        res_sh["status"] == "installed"
        and "Terminal=true" in entry_sh
        and "--fullscreen" in entry_sh
    )
    assert remove_desktop_app("my-studio")["status"] == "uninstalled"

    res_url = install_desktop_app(
        app_id="docs-site", name="Docs Site", exec_path="https://example.org/docs"
    )
    assert (
        res_url["status"] == "installed"
        and "xdg-open https://example.org/docs" in (mock_apps_dir / "docs-site.desktop").read_text()
    )
    assert remove_desktop_app("docs-site")["status"] == "uninstalled"

    res_fp = install_desktop_app(
        app_id="calculator", name="Calculator", exec_path="flatpak:org.gnome.Calculator"
    )
    assert (
        res_fp["status"] == "installed"
        and "flatpak run org.gnome.Calculator" in (mock_apps_dir / "calculator.desktop").read_text()
    )
    assert remove_desktop_app("calculator")["status"] == "uninstalled"
