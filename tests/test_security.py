"""Tests for CoordinateClamper and TokenStore."""

import os
import stat

import pytest

from wayland_computer_use_mcp.security import CoordinateClamper, TokenStore


def test_coordinate_clamper_valid():
    clamper = CoordinateClamper(width=1000, height=800)
    assert clamper.validate_and_clamp(0, 0) == (0, 0)
    assert clamper.validate_and_clamp(500, 400) == (500, 400)
    assert clamper.validate_and_clamp(1000, 800) == (1000, 800)


def test_coordinate_clamper_out_of_bounds():
    clamper = CoordinateClamper(width=1000, height=800)

    with pytest.raises(ValueError, match="out of window bounds"):
        clamper.validate_and_clamp(-1, 400)

    with pytest.raises(ValueError, match="out of window bounds"):
        clamper.validate_and_clamp(500, -1)

    with pytest.raises(ValueError, match="out of window bounds"):
        clamper.validate_and_clamp(1001, 400)

    with pytest.raises(ValueError, match="out of window bounds"):
        clamper.validate_and_clamp(500, 801)


def test_coordinate_clamper_set_bounds():
    clamper = CoordinateClamper(width=100, height=100)
    clamper.set_bounds(1920, 1080)
    assert clamper.dimensions == (1920, 1080)
    assert clamper.validate_and_clamp(1500, 900) == (1500, 900)

    with pytest.raises(ValueError):
        clamper.set_bounds(0, 100)


def test_token_store_crud_and_permissions(tmp_path):
    token_file = tmp_path / "subdir" / "tokens.json"
    store = TokenStore(storage_path=token_file)

    # Verify initial get is None
    assert store.get_token("test-app") is None

    # Set token
    store.set_token("test-app", "tok_xyz123")
    assert store.get_token("test-app") == "tok_xyz123"

    # Set another token
    store.set_token("another-app", "tok_abc789")
    assert store.get_token("another-app") == "tok_abc789"
    assert store.get_token("test-app") == "tok_xyz123"

    # Check directory permissions (0700)
    parent_mode = stat.S_IMODE(os.stat(token_file.parent).st_mode)
    assert parent_mode & 0o777 == 0o700

    # Check file permissions (0600)
    file_mode = stat.S_IMODE(os.stat(token_file).st_mode)
    assert file_mode & 0o777 == 0o600
