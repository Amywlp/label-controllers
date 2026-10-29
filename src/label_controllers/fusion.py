from __future__ import annotations

import math
import re
from typing import Any

import cv2
import numpy as np

Rect = list[int]


def clip_rect(rect: Rect, width: int, height: int) -> Rect:
    x1, y1, x2, y2 = [int(round(v)) for v in rect]
    x1, x2 = sorted((max(0, min(width - 1, x1)), max(0, min(width, x2))))
    y1, y2 = sorted((max(0, min(height - 1, y1)), max(0, min(height, y2))))
    return [x1, y1, max(x1 + 1, x2), max(y1 + 1, y2)]


def rect_area(rect: Rect) -> int:
    return max(0, rect[2] - rect[0]) * max(0, rect[3] - rect[1])


def rect_center(rect: Rect) -> tuple[float, float]:
    return ((rect[0] + rect[2]) / 2.0, (rect[1] + rect[3]) / 2.0)


def union_rects(rects: list[Rect]) -> Rect:
    return [
        min(r[0] for r in rects),
        min(r[1] for r in rects),
        max(r[2] for r in rects),
        max(r[3] for r in rects),
    ]


def intersection_rect(a: Rect, b: Rect) -> Rect | None:
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    if x2 <= x1 or y2 <= y1:
        return None
    return [x1, y1, x2, y2]


def iou(a: Rect, b: Rect) -> float:
    inter = intersection_rect(a, b)
    if inter is None:
        return 0.0
    ia = rect_area(inter)
    return ia / max(1, rect_area(a) + rect_area(b) - ia)


def overlap_fraction(inner: Rect, outer: Rect) -> float:
    inter = intersection_rect(inner, outer)
    if inter is None:
        return 0.0
    return rect_area(inter) / max(1, rect_area(inner))


def contains_point(rect: Rect, point: tuple[float, float], margin: int = 0) -> bool:
    x, y = point
    return rect[0] - margin <= x <= rect[2] + margin and rect[1] - margin <= y <= rect[3] + margin


def axis_gap(a: Rect, b: Rect) -> tuple[int, int]:
    """Return horizontal and vertical edge gaps; zero means overlap on that axis."""
    hgap = max(0, max(a[0], b[0]) - min(a[2], b[2]))
    vgap = max(0, max(a[1], b[1]) - min(a[3], b[3]))
    return hgap, vgap


def _dedupe_boxes(items: list[dict[str, Any]], score_key: str = "conf") -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    for item in sorted(items, key=lambda x: x.get(score_key, 0.0), reverse=True):
        duplicate = False
        for prior in kept:
            a, b = item["box"], prior["box"]
            inter = intersection_rect(a, b)
            if inter is None:
                continue
            ia = rect_area(inter)
            contain = ia / max(1, min(rect_area(a), rect_area(b)))
            if iou(a, b) >= 0.68 or (
                contain >= 0.88 and min(rect_area(a), rect_area(b)) / max(rect_area(a), rect_area(b)) >= 0.45
            ):
                duplicate = True
                break
        if not duplicate:
            kept.append(item)
    return kept


def classify_role(label: str, rect: Rect, image_size: tuple[int, int], subtype: str) -> str:
    text = label.casefold().strip()
    width, height = image_size
    w, h = rect[2] - rect[0], rect[3] - rect[1]

    role_rules = [
        (("搜索", "search"), "search"),
        (("地址", "路径", "address", "path"), "address_bar"),
        (("返回", "后退", "back"), "navigation"),
        (("前进", "forward"), "navigation"),
        (("刷新", "refresh"), "refresh"),
        (("向上", "上一级", "up"), "navigation"),
        (("主页", "home"), "navigation"),
        (("新建", "new"), "create"),
        (("查看", "view"), "view_menu"),
        (("排序", "sort"), "sort_menu"),
        (("剪切", "cut"), "edit_action"),
        (("复制", "copy"), "edit_action"),
        (("粘贴", "paste"), "edit_action"),
        (("删除", "delete"), "destructive_action"),
        (("重命名", "rename"), "edit_action"),
        (("共享", "share"), "share"),
        (("筛选", "filter"), "filter"),
        (("帮助", "help"), "help"),
        (("展开", "折叠", "expand", "collapse"), "disclosure"),
        (("关闭", "close"), "close"),
        (("最小化", "最大化", "还原", "minimize", "maximize"), "window_control"),
    ]
    for words, role in role_rules:
        for word in words:
            if word.isascii():
                if re.search(rf"(?<![a-z]){re.escape(word)}(?![a-z])", text):
                    return role
            elif word in text:
                return role

    if subtype == "text_only" and w > 0.45 * width and h < 0.11 * height:
        return "status_or_banner"
    if w > 0.30 * width and h < 0.10 * height:
        return "address_or_search"
    if subtype == "icon_only":
        return "icon_button"
    if subtype == "container":
        return "container"
    if any(
        text.endswith(ext) for ext in (".txt", ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".png", ".jpg", ".zip", ".exe")
    ):
        return "file_item"
    return "clickable"


def safe_click_point(rect: Rect, obstacles: list[Rect]) -> tuple[int, int]:
    x1, y1, x2, y2 = rect
    w, h = max(1, x2 - x1), max(1, y2 - y1)
    if w < 5 or h < 5:
        return int((x1 + x2) / 2), int((y1 + y2) / 2)

    mask = np.full((h, w), 255, dtype=np.uint8)
    for obstacle in obstacles:
        inter = intersection_rect(rect, obstacle)
        if inter is None:
            continue
        ox1, oy1, ox2, oy2 = inter
        mask[max(0, oy1 - y1) : max(0, oy2 - y1), max(0, ox1 - x1) : max(0, ox2 - x1)] = 0

    if w > 2:
        mask[0, :] = 0
        mask[-1, :] = 0
    if h > 2:
        mask[:, 0] = 0
        mask[:, -1] = 0
    if not np.any(mask):
        return int((x1 + x2) / 2), int((y1 + y2) / 2)

    dist = cv2.distanceTransform(mask, cv2.DIST_L2, 5)
    yy, xx = np.unravel_index(int(np.argmax(dist)), dist.shape)
    return x1 + int(xx), y1 + int(yy)


def _attachment_score(icon: Rect, text: Rect) -> float | None:
    """Score an OCR block as belonging to one detected icon/control."""
    cx, cy = rect_center(text)
    if contains_point(icon, (cx, cy), margin=2):
        # Smallest containing icon wins.
        return 1000.0 - math.log2(max(1, rect_area(icon)))

    inter = intersection_rect(icon, text)
    if inter is not None:
        tf = rect_area(inter) / max(1, rect_area(text))
        if tf >= 0.42:
            return 850.0 + tf * 100.0 - math.log2(max(1, rect_area(icon)))

    ix1, iy1, ix2, iy2 = icon
    tx1, ty1, tx2, ty2 = text
    iw, ih = max(1, ix2 - ix1), max(1, iy2 - iy1)
    tw, th = max(1, tx2 - tx1), max(1, ty2 - ty1)
    hgap, vgap = axis_gap(icon, text)

    # Explorer/file-grid layout: icon above a caption.
    h_overlap = max(0, min(ix2, tx2) - max(ix1, tx1))
    h_ratio = h_overlap / max(1, min(iw, tw))
    vertical_gap = max(0, ty1 - iy2) if ty1 >= iy2 else max(0, iy1 - ty2)
    if h_ratio >= 0.20 and vertical_gap <= max(18, int(0.45 * ih)):
        center_dx = abs((ix1 + ix2) / 2 - (tx1 + tx2) / 2)
        if center_dx <= max(iw * 1.4, tw * 1.4, 90):
            return 700.0 - vertical_gap * 4.0 + h_ratio * 50.0 - center_dx * 0.5

    # Toolbar/button layout: icon and caption sit on the same baseline.
    v_overlap = max(0, min(iy2, ty2) - max(iy1, ty1))
    v_ratio = v_overlap / max(1, min(ih, th))
    center_dy = abs((iy1 + iy2) / 2 - (ty1 + ty2) / 2)
    if v_ratio >= 0.35 and hgap <= max(16, int(0.30 * max(iw, tw))):
        if center_dy <= max(ih * 0.8, th * 0.8, 18):
            return 620.0 - hgap * 4.0 + v_ratio * 40.0 - center_dy

    # Conservative proximity fallback for compact controls.
    dx = max(0, max(ix1, tx1) - min(ix2, tx2))
    dy = max(0, max(iy1, ty1) - min(iy2, ty2))
    distance = math.hypot(dx, dy)
    max_dim = max(iw, ih, tw, th)
    if distance <= max(24, int(max_dim * 0.55)):
        return 400.0 - distance * 4.0
    return None


def fuse_elements(
    raw_icons: list[dict[str, Any]],
    raw_texts: list[dict[str, Any]],
    image_size: tuple[int, int],
    screen_origin: tuple[int, int] = (0, 0),
) -> dict[str, Any]:
    width, height = image_size
    image_area = width * height

    icons = []
    for i, item in enumerate(raw_icons):
        box = clip_rect(item["box"], width, height)
        icons.append({"icon_index": i, "box": box, "conf": float(item.get("conf", 0.0))})
    icons = _dedupe_boxes(icons)

    texts = []
    for i, item in enumerate(raw_texts):
        label = " ".join(str(item.get("text", "")).strip().split())
        if not label:
            continue
        texts.append(
            {
                "text_index": i,
                "box": clip_rect(item["box"], width, height),
                "text": label,
                "conf": float(item.get("conf", 0.0)),
            }
        )
    texts = _dedupe_boxes(texts)

    # Large detector outputs are panels/containers, not single click targets.
    containers: list[dict[str, Any]] = []
    actionable_icons: list[dict[str, Any]] = []
    for icon in icons:
        contained = [t for t in texts if contains_point(icon["box"], rect_center(t["box"]), margin=2)]
        huge = rect_area(icon["box"]) >= image_area * 0.18
        wide_panel = (
            len(contained) >= 4
            and (icon["box"][2] - icon["box"][0]) >= width * 0.35
            and (icon["box"][3] - icon["box"][1]) >= height * 0.10
        )
        if huge or wide_panel:
            containers.append(icon)
        else:
            actionable_icons.append(icon)

    assignments: dict[int, list[dict[str, Any]]] = {i: [] for i in range(len(actionable_icons))}
    used_text: set[int] = set()

    # Greedy global ranking avoids the same OCR block being attached twice.
    candidates = []
    for ti, text in enumerate(texts):
        for ii, icon in enumerate(actionable_icons):
            score = _attachment_score(icon["box"], text["box"])
            if score is not None:
                candidates.append((score, ti, ii))
    for _score, ti, ii in sorted(candidates, reverse=True):
        if ti in used_text or assignments[ii]:
            continue
        # One icon gets its single best-aligned OCR label.
        assignments[ii].append(texts[ti])
        used_text.add(ti)

    elements: list[dict[str, Any]] = []
    for ii, icon in enumerate(actionable_icons):
        attached = sorted(assignments[ii], key=lambda t: (t["box"][1], t["box"][0]))
        labels = []
        for text in attached:
            if text["text"] not in labels:
                labels.append(text["text"])
        label = " ".join(labels)
        boxes = [icon["box"]] + [t["box"] for t in attached]
        combined = union_rects(boxes)
        subtype = "with_text" if label else "icon_only"
        elements.append(
            {
                "subtype": subtype,
                "label": label,
                "role": classify_role(label, combined, image_size, subtype),
                "conf": round(icon["conf"], 4),
                "rect": combined,
                "icon_rect": icon["box"],
                "text_rects": [t["box"] for t in attached],
                "source": "yolo+ocr" if attached else "yolo",
                "clickable": True,
                "_ocr_conf": [t["conf"] for t in attached],
            }
        )

    for ti, text in enumerate(texts):
        if ti in used_text:
            continue
        subtype = "text_only"
        elements.append(
            {
                "subtype": subtype,
                "label": text["text"],
                "role": classify_role(text["text"], text["box"], image_size, subtype),
                "conf": round(text["conf"], 4),
                "rect": text["box"],
                "icon_rect": None,
                "text_rects": [text["box"]],
                "source": "ocr",
                "clickable": True,
                "_ocr_conf": [text["conf"]],
            }
        )

    for icon in containers:
        contained = [t for t in texts if contains_point(icon["box"], rect_center(t["box"]), margin=2)]
        label = " ".join(dict.fromkeys(t["text"] for t in contained))[:160]
        elements.append(
            {
                "subtype": "container",
                "label": label,
                "role": "container",
                "conf": round(icon["conf"], 4),
                "rect": icon["box"],
                "icon_rect": icon["box"],
                "text_rects": [t["box"] for t in contained],
                "source": "yolo+ocr" if contained else "yolo",
                "clickable": False,
                "_ocr_conf": [t["conf"] for t in contained],
            }
        )

    # Reading order with a small vertical bucket to keep rows coherent.
    elements.sort(key=lambda e: (round(rect_center(e["rect"])[1] / 24), rect_center(e["rect"])[0]))

    clickable_rects = [e["rect"] for e in elements if e.get("clickable")]
    for idx, element in enumerate(elements, 1):
        if element["clickable"]:
            own = element["rect"]
            obstacles = [r for r in clickable_rects if r is not own][:300]
            # OCR text is the strongest click anchor for a control. Using the
            # union rect can place the point in the blank gap between an icon
            # and a caption, or on the boundary of the next table row.
            text_rects = element.get("text_rects") or []
            click_region = max(text_rects, key=rect_area) if text_rects else own
            click = safe_click_point(click_region, obstacles)
        else:
            click = (int(rect_center(element["rect"])[0]), int(rect_center(element["rect"])[1]))

        cx, cy = rect_center(element["rect"])
        element["id"] = idx
        element["center"] = [int(round(cx)), int(round(cy))]
        element["click"] = [int(click[0]), int(click[1])]
        element["screen_click"] = [int(click[0] + screen_origin[0]), int(click[1] + screen_origin[1])]
        element["ocr_conf"] = round(float(np.mean(element.pop("_ocr_conf"))), 4) if element.get("_ocr_conf") else None

    # Stable public field order.
    ordered_fields = [
        "id",
        "subtype",
        "label",
        "role",
        "conf",
        "ocr_conf",
        "clickable",
        "rect",
        "center",
        "click",
        "screen_click",
        "source",
        "icon_rect",
        "text_rects",
    ]
    public = [{key: e.get(key) for key in ordered_fields} for e in elements]
    counts: dict[str, int] = {}
    for e in public:
        counts[e["subtype"]] = counts.get(e["subtype"], 0) + 1
    return {
        "image_size": [width, height],
        "screen_origin": [int(screen_origin[0]), int(screen_origin[1])],
        "counts": {
            "icons_raw": len(raw_icons),
            "icons_deduplicated": len(icons),
            "texts_raw": len(raw_texts),
            "texts_deduplicated": len(texts),
            "containers": counts.get("container", 0),
            "with_text": counts.get("with_text", 0),
            "icon_only": counts.get("icon_only", 0),
            "text_only": counts.get("text_only", 0),
            "controls": sum(1 for e in public if e["clickable"]),
            "elements": len(public),
        },
        "elements": public,
    }
