"""Set-of-Marks (SoM) visual overlay rendering on captured image frames.

Draws indexed bounding boxes, tags, and generates an element legend
for Vision-Language Model grounding and navigation.
"""

from __future__ import annotations

import logging
from typing import Any

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)


def load_overlay_font() -> ImageFont.ImageFont:
    """Loads a crisp TrueType or default bitmap font for bounding box tags."""
    try:
        # Try finding standard clean TrueType fonts on Linux
        for font_path in [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
        ]:
            try:
                return ImageFont.truetype(font_path, size=11)
            except Exception:
                continue
    except Exception:
        pass
    return ImageFont.load_default()


def draw_labeled_overlay(
    frame: Image.Image,
    elements: list[dict[str, Any]],
    root_bounds: list[int] | None = None,
) -> tuple[Image.Image, list[dict[str, Any]]]:
    """Draws Set-of-Marks bounding boxes and index badges onto a frame.

    Args:
        frame: Base RGBA or RGB PIL Image.
        elements: List of interactive element dictionaries containing 'bounds' and 'center'.
        root_bounds: Optional [x, y, w, h] of the root container for resolution scaling.

    Returns:
        (annotated_composite_image, legend_metadata)
    """
    root_w = root_bounds[2] if root_bounds and len(root_bounds) >= 4 else 0
    root_h = root_bounds[3] if root_bounds and len(root_bounds) >= 4 else 0
    scale_x = frame.width / float(root_w) if root_w > 0 else 1.0
    scale_y = frame.height / float(root_h) if root_h > 0 else 1.0

    annotated = frame.copy().convert("RGBA")
    overlay = Image.new("RGBA", annotated.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font = load_overlay_font()

    legend: list[dict[str, Any]] = []
    for idx, el in enumerate(elements, start=1):
        raw_x, raw_y, raw_w, raw_h = el.get("bounds", [0, 0, 0, 0])
        if scale_x != 1.0 or scale_y != 1.0:
            x = int(raw_x * scale_x)
            y = int(raw_y * scale_y)
            w = int(raw_w * scale_x)
            h = int(raw_h * scale_y)
            cx = x + w // 2
            cy = y + h // 2
        else:
            x, y, w, h = raw_x, raw_y, raw_w, raw_h
            cx, cy = el.get("center", [x + w // 2, y + h // 2])

        # Bounding outline
        draw.rectangle([x, y, x + w, y + h], outline=(0, 206, 201, 220), width=2)
        tag = f"[{idx}]"
        bbox = draw.textbbox((0, 0), tag, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        bx1 = max(0, x)
        by1 = y - th - 6
        if by1 < 0:
            by1 = y + 2
        bx2 = bx1 + tw + 8
        by2 = by1 + th + 6
        # Badge background and text
        draw.rounded_rectangle([bx1, by1, bx2, by2], radius=4, fill=(214, 48, 49, 230))
        draw.text((bx1 + 4, by1 + 3), tag, fill=(255, 255, 255, 255), font=font)

        legend.append(
            {
                "index": idx,
                "role": el.get("role", "widget"),
                "name": el.get("name", ""),
                "center": [cx, cy],
                "bounds": [x, y, w, h],
            }
        )

    final_img = Image.alpha_composite(annotated, overlay)
    return final_img, legend
