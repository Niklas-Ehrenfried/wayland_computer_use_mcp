#!/usr/bin/env bash

# Exit immediately if a command exits with a non-zero status
set -e

echo "Starting setup for wayland_computer_use_mcp..."

# Optional system runtime tools for virtual-window blackbox and AT-SPI tree inspection
if command -v apt-get >/dev/null 2>&1; then
    echo "Installing optional Wayland runtime dependencies (at-spi2-core, dbus, labwc)..."
    sudo apt-get update
    sudo apt-get install -y --no-install-recommends \
        dbus-x11 \
        at-spi2-core \
        labwc
fi

echo "Syncing pure-Python core & dev dependencies with uv..."
uv sync --all-extras --dev

echo "Setup complete! Run the MCP server via:"
echo "  uv run wayland-computer-use-mcp --virtual-window"
