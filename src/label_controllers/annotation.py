from __future__ import annotations

import io
import os
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

ROLE_COLORS: dict[str, tuple[int, int, int]] = {
    "search": (14, 165, 233),
    "address_bar": (14, 165, 233),
    "address_or_search": (14, 165, 233),
    "navigation": (59, 130, 246),
    "refresh": (6, 182, 212),
    "create": (34, 197, 94),
    "view_menu": (16, 185, 129),
    "sort_menu": (20, 184, 166),
    "edit_action": (132, 204, 22),
    "destructive_action": (239, 68, 68),
    "share": (168, 85, 247),
    "filter": (99, 102, 241),
    "help": (100, 116, 139),
    "disclosure": (245, 158, 11),
    "close": (220, 38, 38),
    "window_control": (148, 163, 184),
    "status_or_banner": (107, 114, 128),
    "icon_button": (249, 115, 22),
    "clickable": (249, 115, 22),
    "file_item": (234, 179, 8),
    "container": (120, 120, 120),
}


def _font_candidates() -> list[Path]:
    configured = os.getenv("LABEL_CONTROLLERS_FONT", "").strip()
    return [
        *([Path(configured)] if configured else []),
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
        Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in _font_candidates():
        try:
            if candidate.is_file():
                return ImageFont.truetype(str(candidate), size)
        except OSError:
            continue
    return ImageFont.load_default()


def _safe_label(text: str, limit: int = 28) -> str:
    value = " ".join(str(text).split())
    return value if len(value) <= limit else value[: limit - 3] + "..."


def _overlaps(a: list[int], b: list[int], margin: int = 2) -> bool:
    return not (a[2] + margin <= b[0] or b[2] + margin <= a[0] or a[3] + margin <= b[1] or b[3] + margin <= a[1])


def _place_badge(
    draw: ImageDraw.ImageDraw,
    image_size: tuple[int, int],
    element_rect: list[int],
    label: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    padding: int,
    occupied: list[list[int]],
) -> tuple[list[int], tuple[int, int]] | None:
    bbox = draw.textbbox((0, 0), label, font=font)
    badge_width = bbox[2] - bbox[0] + padding * 2
    badge_height = bbox[3] - bbox[1] + padding * 2
    x1, y1, x2, y2 = element_rect
    candidates = [
        (x1, y1 - badge_height - 2),
        (x1, y2 + 2),
        (x2 + 3, y1),
        (x1 - badge_width - 3, y1),
        (x1 + 2, y1 + 2),
        (x1 + 2, y2 - badge_height - 2),
    ]
    for raw_x, raw_y in candidates:
        x = max(0, min(image_size[0] - badge_width, int(round(raw_x))))
        y = max(0, min(image_size[1] - badge_height, int(round(raw_y))))
        candidate = [x, y, x + badge_width, y + badge_height]
        if any(_overlaps(candidate, prior) for prior in occupied):
            continue
        occupied.append(candidate)
        return candidate, (x + padding, y + padding - 1)
    return None


def render_annotation(
    image_path: Path,
    elements: list[dict[str, Any]],
    *,
    control_count: int,
    text_count: int,
    font_path: Path | None = None,
) -> bytes:
    """Draw boxes, safe click points and collision-aware compact labels."""
    if font_path is not None and font_path.is_file():
        os.environ["LABEL_CONTROLLERS_FONT"] = str(font_path)

    with Image.open(image_path) as source:
        image = source.convert("RGB")

    scale = max(0.7, min(2.0, max(image.width, image.height) / 1600))
    line_width = max(2, int(round(2 * scale)))
    font = _load_font(max(14, int(round(17 * scale))))
    dot_radius = max(3, int(round(4 * scale)))
    padding = max(3, int(round(4 * scale)))
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    for element in elements:
        x1, y1, x2, y2 = [int(round(v)) for v in element["rect"]]
        role = str(element.get("role") or "clickable")
        subtype = str(element.get("subtype") or "")
        color = ROLE_COLORS.get(role) or ROLE_COLORS.get(subtype) or (236, 72, 153)
        alpha = 170 if subtype == "container" else 235
        draw.rectangle(
            [x1, y1, x2, y2],
            outline=(*color, alpha),
            width=max(1, line_width - 1 if subtype == "container" else line_width),
        )
        if subtype == "container":
            continue
        click_x, click_y = [int(round(v)) for v in element["click"]]
        draw.ellipse(
            [click_x - dot_radius, click_y - dot_radius, click_x + dot_radius, click_y + dot_radius],
            fill=(239, 68, 68, 255),
            outline=(255, 255, 255, 255),
            width=max(1, line_width // 2),
        )

    banner = f"Label Controllers  |  controls: {control_count}  |  OCR text: {text_count}"
    banner_bbox = draw.textbbox((0, 0), banner, font=font)
    banner_rect = [10, 10, 32 + banner_bbox[2] - banner_bbox[0], 24 + banner_bbox[3] - banner_bbox[1]]
    occupied = [banner_rect]

    # Larger, higher-confidence controls receive labels first; boxes are still drawn for all.
    candidates = [element for element in elements if element.get("subtype") != "container"]
    candidates.sort(
        key=lambda element: (
            not bool(element.get("clickable")),
            -((element["rect"][2] - element["rect"][0]) * (element["rect"][3] - element["rect"][1])),
            element["rect"][1],
            element["rect"][0],
        )
    )
    max_labels = int(os.getenv("LABEL_CONTROLLERS_ANNOTATION_MAX_LABELS", "90"))
    drawn_labels = 0
    for element in candidates:
        if drawn_labels >= max_labels:
            break
        role = str(element.get("role") or "clickable")
        text = _safe_label(str(element.get("label") or role))
        label = f"#{element['id']} {text}"
        color = ROLE_COLORS.get(role) or ROLE_COLORS.get(str(element.get("subtype"))) or (236, 72, 153)
        placement = _place_badge(
            draw,
            image.size,
            [int(round(v)) for v in element["rect"]],
            label,
            font,
            padding,
            occupied,
        )
        if placement is None:
            continue
        badge, text_position = placement
        draw.rounded_rectangle(badge, radius=max(2, padding), fill=(*color, 242))
        draw.text(text_position, label, font=font, fill=(255, 255, 255, 255))
        drawn_labels += 1

    draw.rounded_rectangle(banner_rect, radius=8, fill=(15, 23, 42, 230))
    draw.text((banner_rect[0] + 11, banner_rect[1] + 7), banner, font=font, fill=(248, 250, 252, 255))

    composed = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
    buffer = io.BytesIO()
    composed.save(buffer, format="PNG", optimize=False, compress_level=6)
    return buffer.getvalue()
