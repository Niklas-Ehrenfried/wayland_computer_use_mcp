#!/usr/bin/env bash

# Exit immediately if a command exits with a non-zero status
set -e

echo "🚀 Starting setup for wayland_computer_use_mcp..."

# 1. Update system package lists
echo "🔄 Updating package lists..."
sudo apt update

# 2. Install all system dependencies required to build pycairo and pygobject
echo "📦 Installing system dependencies (C-libraries & build tools)..."
sudo apt install -y \
    pkg-config \
    libcairo2-dev \
    gobject-introspection \
    libgirepository1.0-dev \
    libgirepository-2.0-dev \
    gir1.2-girepository-2.0

# 3. Create uv virtual environment if it doesn't exist
if [ ! -d ".venv" ]; then
    echo "🌱 Creating virtual environment with uv..."
    uv sync
else
    echo "✅ Virtual environment (.venv) already exists."
fi

# 4. Install project dependencies in editable mode
echo "🛠️ Installing Python project dependencies via uv..."
uv pip install -e .

echo "🎉 Setup complete! To activate your environment, run:"
echo "   source .venv/bin/activate"
