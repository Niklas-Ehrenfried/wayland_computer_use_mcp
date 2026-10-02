"""ScreenCast pipeline and frame capture via GStreamer and PipeWire."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from PIL import Image

from wayland_computer_use_mcp.config import get_config

logger = logging.getLogger(__name__)

# Try importing GStreamer via PyGObject
_GST_AVAILABLE = False
try:
    import gi

    gi.require_version("Gst", "1.0")
    from gi.repository import Gst  # type: ignore

    Gst.init(None)
    _GST_AVAILABLE = True
except Exception as exc:
    logger.warning("GStreamer PyGObject bindings unavailable: %s", exc)

DEFAULT_SAVE_DIR = Path.home() / ".cache" / "wayland-computer-use-mcp" / "screenshots"


def capture_native_desktop_frame() -> Image.Image | None:
    """Captures the active Wayland desktop frame using native compositor CLI utilities.

    Supports:
    - KDE Plasma: spectacle (-b -n -o <path>)
    - wlroots / Sway: grim (<path>)
    - GNOME: gnome-screenshot (-f <path>)
    """
    cfg = get_config()
    if cfg.mock_mode or not os.environ.get("WAYLAND_DISPLAY"):
        return None

    cmd_template: list[str] | None = None
    if shutil.which("spectacle"):
        cmd_template = ["spectacle", "-b", "-n", "-o"]
    elif shutil.which("grim"):
        cmd_template = ["grim"]
    elif shutil.which("gnome-screenshot"):
        cmd_template = ["gnome-screenshot", "-f"]

    if not cmd_template:
        return None

    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
            tmp_path = tf.name

        cmd = [*cmd_template, tmp_path]
        proc = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3.0,
        )
        if proc.returncode == 0 and os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 0:
            with Image.open(tmp_path) as img:
                return img.convert("RGB")
    except Exception as exc:
        logger.debug("Native screenshot capture failed with %s: %s", cmd_template[0], exc)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    return None


def crop_element(
    frame: Image.Image,
    x: int,
    y: int,
    w: int,
    h: int,
) -> Image.Image:
    """Crops the frame to requested bounds [x, y, w, h] clamped to surface boundaries."""
    img_w, img_h = frame.size
    clamped_x1 = max(0, min(x, img_w))
    clamped_y1 = max(0, min(y, img_h))
    clamped_x2 = max(clamped_x1, min(x + w, img_w))
    clamped_y2 = max(clamped_y1, min(y + h, img_h))

    if clamped_x2 <= clamped_x1 or clamped_y2 <= clamped_y1:
        # Return minimal 1x1 image if crop is zero-sized
        return Image.new("RGBA", (1, 1), (0, 0, 0, 0))

    return frame.crop((clamped_x1, clamped_y1, clamped_x2, clamped_y2))


class ScreenCastPipeline:
    """Manages GStreamer PipeWire appsink pipeline and frame capture."""

    def __init__(self) -> None:
        self._gst_pipeline: Any = None
        self._gst_appsink: Any = None
        self._last_frame: Image.Image | None = None
        self.last_saved_frame_path: str | None = None

    def setup_pipeline(self, node_id: int) -> None:
        """Build and start minimal GStreamer PipeWire appsink pipeline."""
        if not _GST_AVAILABLE:
            logger.warning("GStreamer unavailable, skipping pipeline setup.")
            return

        pipeline_desc = (
            f"pipewiresrc path={node_id} keepalive-time=1000 resend-last=true ! "
            f"videoconvert ! video/x-raw,format=RGBA ! "
            f"appsink name=sink emit-signals=True max-buffers=1 drop=True"
        )
        try:
            self._gst_pipeline = Gst.parse_launch(pipeline_desc)
            self._gst_appsink = self._gst_pipeline.get_by_name("sink")
            self._gst_pipeline.set_state(Gst.State.PLAYING)
            logger.info("GStreamer pipeline started for node %d", node_id)
        except Exception as exc:
            logger.warning("GStreamer pipeline initialization failed: %s", exc)

    def _pull_gst_frame(self) -> Image.Image | None:
        """Pulls the latest RGBA/RGB frame from the GStreamer appsink."""
        if not self._gst_appsink or not _GST_AVAILABLE:
            return None

        try:
            sample = None
            for _ in range(10):
                sample = self._gst_appsink.emit("try-pull-sample", 100000000)
                if sample:
                    break
            if not sample:
                return None

            buf = sample.get_buffer()
            caps = sample.get_caps()
            structure = caps.get_structure(0)
            w = structure.get_value("width")
            h = structure.get_value("height")
            success, map_info = buf.map(Gst.MapFlags.READ)
            if not success:
                return None

            fmt = structure.get_value("format") if structure.has_field("format") else "RGBA"
            if fmt == "RGBA":
                frame = Image.frombytes("RGBA", (w, h), map_info.data, "raw", "RGBA").convert("RGB")
            elif fmt == "BGRA":
                frame = Image.frombytes("RGBA", (w, h), map_info.data, "raw", "BGRA").convert("RGB")
            elif fmt == "BGRx":
                frame = Image.frombytes("RGB", (w, h), map_info.data, "raw", "BGRX")
            elif fmt == "RGBx":
                frame = Image.frombytes("RGB", (w, h), map_info.data, "raw", "RGBX")
            elif fmt == "RGB":
                frame = Image.frombytes("RGB", (w, h), map_info.data, "raw", "RGB")
            elif fmt == "BGR":
                frame = Image.frombytes("RGB", (w, h), map_info.data, "raw", "BGR")
            else:
                frame = Image.frombytes("RGBA", (w, h), map_info.data, "raw", "RGBA").convert("RGB")
            buf.unmap(map_info)
            return frame
        except Exception as exc:
            logger.debug("Failed to pull GStreamer sample: %s", exc)
            return None

    def pull_frame(self) -> Image.Image | None:
        """Pulls the latest frame from GStreamer appsink, falling back to native capture."""
        frame = self._pull_gst_frame()
        if frame is not None:
            self._last_frame = frame
            return frame

        # Fallback to native compositor screenshot (spectacle, grim, gnome-screenshot)
        native = capture_native_desktop_frame()
        if native is not None:
            self._last_frame = native
            return native

        return self._last_frame

    def save_frame(self, frame: Image.Image, filename: str = "latest_capture.png") -> str | None:
        """Persists captured frame to disk and updates last_saved_frame_path."""
        save_dir_env = os.environ.get("WAYLAND_MCP_SAVE_FRAMES_DIR")
        save_dir = Path(save_dir_env) if save_dir_env else DEFAULT_SAVE_DIR
        try:
            save_dir.mkdir(parents=True, exist_ok=True)
            frame_path = save_dir / filename
            frame.save(frame_path)
            self.last_saved_frame_path = str(frame_path)
            return self.last_saved_frame_path
        except Exception as exc:
            logger.debug("Failed to save frame: %s", exc)
            return None

    def stop(self) -> None:
        """Stops GStreamer pipeline."""
        if self._gst_pipeline and _GST_AVAILABLE:
            try:
                self._gst_pipeline.set_state(Gst.State.NULL)
            except Exception:
                pass
            self._gst_pipeline = None
            self._gst_appsink = None
