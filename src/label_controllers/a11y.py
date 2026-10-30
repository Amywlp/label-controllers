"""Accessibility-tree (a11y) channel: collection, probing and alignment.

The vision channel (YOLO icons + RapidOCR text) answers *where* the controls
are. The accessibility channel answers *what* they are: role, name and exact
border box. This module adds the third leg so ``analyze`` can merge all three
sources into one element list.

Two ways to get an a11y tree:

* ``mode="cdp"``     - the service connects to a Chrome/Electron DevTools
  endpoint, pulls ``Accessibility.getFullAXTree`` and resolves border boxes.
* ``mode="inline"``  - the caller already collected the tree and posts it as
  ``{"elements": [{"role": ..., "name": ..., "rect": [x1, y1, x2, y2]}]}``.

Design rules that keep the merge trustworthy:

1. Spatial matching uses IoU **and** containment, because a vision box often
   covers the text of a button while the a11y box covers the whole button.
2. Text comparison strips icon-font private-use codepoints (U+E000-U+F8FF);
   a11y names frequently start with ``\\ue6xx`` glyphs.
3. Missing entries are not errors: each channel legitimately sees things the
   other does not (canvas vs footer links). Only *conflicting names* count as
   degenerate matches and they are reported, never silently overwritten.
"""

from __future__ import annotations

import difflib
import json
import re
import urllib.request
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

INTERACTIVE_ROLES = frozenset(
    {
        "button",
        "link",
        "textbox",
        "searchbox",
        "checkbox",
        "radio",
        "combobox",
        "listbox",
        "menuitem",
        "menuitemcheckbox",
        "menuitemradio",
        "tab",
        "switch",
        "slider",
        "spinbutton",
        "treeitem",
        "option",
    }
)

_ICON_GLYPH_RE = re.compile(r"[\ue000-\uf8ff]")
_NORMALISE_RE = re.compile(r"[\s\u3000·|｜/\\\-—_:：()（）\[\]【】\"'“”‘’,，.。!！?？]+")

# Probe thresholds. These are empirical, calibrated on real pages; they are
# exposed in the report so a caller can see *why* a verdict was given.
MIN_AX_NODES = 20
MIN_INTERACTIVE_COVERAGE = 0.60
MIN_NAMED_RATE = 0.80
MIN_COORD_RATE = 0.90
MIN_ALIGN_RATE = 0.60


class A11yError(RuntimeError):
    """Raised when the accessibility channel cannot deliver a usable tree."""

    def __init__(self, message: str, *, code: str = "a11y_unavailable", details: Any = None):
        super().__init__(message)
        self.code = code
        self.details = details


# --------------------------------------------------------------------------- #
# CDP transport
# --------------------------------------------------------------------------- #


def _import_websocket():
    try:
        import websocket  # type: ignore[import-not-found]
    except ModuleNotFoundError as exc:  # pragma: no cover - dependency hint
        raise A11yError(
            "The a11y channel needs the optional dependency websocket-client. "
            'Install it with: pip install "label-controllers[a11y]"',
            code="a11y_dependency_missing",
        ) from exc
    return websocket


class CDPClient:
    """Minimal synchronous DevTools Protocol client (one request at a time)."""

    def __init__(self, ws_url: str, *, timeout: float = 20.0):
        websocket = _import_websocket()
        try:
            # Chrome rejects WebSocket upgrades whose Origin header it does not
            # trust (HTTP 403). Suppressing the origin is the portable fix and
            # avoids requiring --remote-allow-origins on the browser.
            self._ws = websocket.create_connection(
                ws_url, timeout=timeout, max_size=None, suppress_origin=True
            )
        except Exception as exc:  # noqa: BLE001 - surfaced as A11yError
            raise A11yError(f"Cannot open a CDP session at {ws_url}: {exc}", code="a11y_cdp_connect") from exc
        self._id = 0

    def call(self, method: str, **params: Any) -> dict[str, Any]:
        self._id += 1
        self._ws.send(json.dumps({"id": self._id, "method": method, "params": params}))
        while True:
            message = json.loads(self._ws.recv())
            if message.get("id") != self._id:
                continue  # protocol events are interleaved with responses
            if "error" in message:
                raise A11yError(f"{method} failed: {message['error']}", code="a11y_cdp_error")
            return message.get("result", {})

    def close(self) -> None:
        try:
            self._ws.close()
        except Exception:  # noqa: BLE001
            pass


def _list_targets(cdp_url: str, timeout: float) -> list[dict[str, Any]]:
    url = cdp_url.rstrip("/") + "/json/list"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            targets = json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise A11yError(f"Cannot reach a DevTools endpoint at {cdp_url}: {exc}", code="a11y_cdp_connect") from exc
    pages = [item for item in targets if item.get("type") == "page"]
    if not pages:
        raise A11yError("The DevTools endpoint exposes no page target.", code="a11y_no_page_target")
    return pages


def _border_box(client: CDPClient, backend_node_id: int | None) -> list[int] | None:
    if not backend_node_id:
        return None
    try:
        model = client.call("DOM.getBoxModel", backendNodeId=backend_node_id)["model"]["border"]
    except A11yError:
        return None
    xs, ys = model[0::2], model[1::2]
    if not xs or not ys:
        return None
    return [round(min(xs)), round(min(ys)), round(max(xs)), round(max(ys))]


def _select_page(
    pages: list[dict[str, Any]],
    *,
    target: int = 0,
    target_url: str | None = None,
) -> dict[str, Any]:
    """Pick a page target by URL substring first, falling back to the index.

    Real desktops keep many tabs open, so ``target`` alone is unstable: Chrome
    sorts /json/list by recency. ``target_url`` pins the page deterministically.
    """

    if target_url:
        needle = target_url.strip().lower()
        for page in pages:
            if needle and needle in str(page.get("url") or "").lower():
                return page
        raise A11yError(
            f"No page target matches url substring {target_url!r}.",
            code="a11y_no_page_target",
            details={"open_pages": [page.get("url") for page in pages][:20]},
        )
    return pages[min(max(target, 0), len(pages) - 1)]


def collect_from_cdp(
    cdp_url: str,
    *,
    target: int = 0,
    target_url: str | None = None,
    url: str | None = None,
    timeout: float = 20.0,
    navigate_wait: float = 2.5,
) -> dict[str, Any]:
    """Pull the interactive a11y nodes (role/name/border box) from a live page."""

    pages = _list_targets(cdp_url, timeout)
    page = _select_page(pages, target=target, target_url=target_url)
    client = CDPClient(page["webSocketDebuggerUrl"], timeout=timeout)
    try:
        if url:
            client.call("Page.navigate", url=url)
            import time

            time.sleep(navigate_wait)

        tree = client.call("Accessibility.getFullAXTree").get("nodes", [])
        elements: list[dict[str, Any]] = []
        coord_failed = 0
        for node in tree:
            role = (node.get("role") or {}).get("value")
            if role not in INTERACTIVE_ROLES:
                continue
            name = (node.get("name") or {}).get("value") or ""
            rect = _border_box(client, node.get("backendDOMNodeId"))
            if rect is None:
                coord_failed += 1
            elements.append(
                {
                    "role": role,
                    "name": name,
                    "rect": rect,
                    "backend_node_id": node.get("backendDOMNodeId"),
                }
            )

        viewport: list[Any] = [None, None, None]
        try:
            metrics = client.call(
                "Runtime.evaluate",
                expression="[innerWidth, innerHeight, devicePixelRatio]",
                returnByValue=True,
            )
            viewport = metrics.get("result", {}).get("value") or viewport
        except A11yError:
            pass

        roles: dict[str, int] = {}
        for node in tree:
            role = (node.get("role") or {}).get("value") or "unknown"
            roles[role] = roles.get(role, 0) + 1

        return {
            "mode": "cdp",
            "endpoint": cdp_url,
            "target": {"title": page.get("title"), "url": page.get("url")},
            "viewport": viewport,
            "node_count": len(tree),
            "role_counts": dict(sorted(roles.items(), key=lambda item: -item[1])[:12]),
            "coord_failed": coord_failed,
            "elements": elements,
        }
    finally:
        client.close()


# --------------------------------------------------------------------------- #
# Inline payload normalisation
# --------------------------------------------------------------------------- #


def _coerce_rect(value: Any) -> list[int] | None:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes) or len(value) != 4:
        return None
    try:
        x1, y1, x2, y2 = (float(item) for item in value)
    except (TypeError, ValueError):
        return None
    if x2 <= x1 or y2 <= y1:
        return None
    return [round(min(x1, x2)), round(min(y1, y2)), round(max(x1, x2)), round(max(y1, y2))]


def normalize_inline(payload: Any) -> dict[str, Any]:
    """Validate a caller-supplied accessibility tree.

    Accepts ``{"elements": [...]}`` or a bare list. Every element needs a role;
    ``name``/``rect`` are optional but strongly recommended. Elements without a
    rect are kept for statistics yet cannot participate in alignment.
    """

    if isinstance(payload, Mapping):
        raw_elements = payload.get("elements")
        extra = {key: value for key, value in payload.items() if key != "elements"}
    elif isinstance(payload, Sequence) and not isinstance(payload, str | bytes):
        raw_elements, extra = payload, {}
    else:
        raise A11yError("Inline a11y payload must be a list or an object with 'elements'.", code="a11y_invalid_payload")

    if not isinstance(raw_elements, Sequence) or isinstance(raw_elements, str | bytes):
        raise A11yError("Inline a11y payload has no element list.", code="a11y_invalid_payload")

    elements: list[dict[str, Any]] = []
    for item in raw_elements:
        if not isinstance(item, Mapping):
            continue
        role = str(item.get("role") or "").strip()
        if not role:
            continue
        elements.append(
            {
                "role": role,
                "name": str(item.get("name") or "").strip(),
                "rect": _coerce_rect(item.get("rect") or item.get("box")),
                "backend_node_id": item.get("backend_node_id") or item.get("backendNodeId"),
            }
        )
    if not elements:
        raise A11yError("Inline a11y payload contained no usable element.", code="a11y_empty_tree")

    return {
        "mode": "inline",
        **extra,
        "node_count": int(extra.get("node_count") or len(elements)),
        "elements": elements,
    }


def collect(spec: Mapping[str, Any] | str | None) -> dict[str, Any] | None:
    """Resolve an ``a11y`` request section into a collected tree, if requested."""

    if not spec:
        return None
    if isinstance(spec, str):
        try:
            spec = json.loads(spec)
        except json.JSONDecodeError as exc:
            raise A11yError("The a11y form field must be JSON.", code="a11y_invalid_payload") from exc
    if not isinstance(spec, Mapping):
        raise A11yError("The a11y section must be an object.", code="a11y_invalid_payload")

    mode = str(spec.get("mode") or "").strip().lower()
    if not mode:
        mode = "cdp" if spec.get("cdp_url") else ("inline" if spec.get("elements") else "off")
    if mode in {"off", "none", "disabled"}:
        return None
    if mode == "inline":
        return normalize_inline(spec)
    if mode == "cdp":
        cdp_url = str(spec.get("cdp_url") or "").strip()
        if not cdp_url:
            raise A11yError("mode='cdp' requires cdp_url.", code="a11y_invalid_payload")
        return collect_from_cdp(
            cdp_url,
            target=int(spec.get("target") or 0),
            target_url=spec.get("target_url") or None,
            url=spec.get("url") or None,
            timeout=float(spec.get("timeout_seconds") or 20.0),
        )
    raise A11yError(f"Unknown a11y mode: {mode}", code="a11y_invalid_payload")


# --------------------------------------------------------------------------- #
# Text / geometry helpers
# --------------------------------------------------------------------------- #


def normalise_text(value: str | None) -> str:
    text = _ICON_GLYPH_RE.sub("", value or "")
    return _NORMALISE_RE.sub("", text.lower())


def text_similarity(left: str | None, right: str | None) -> float:
    a, b = normalise_text(left), normalise_text(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.85
    return difflib.SequenceMatcher(None, a, b).ratio()


def _area(rect: Sequence[int]) -> int:
    return max((rect[2] - rect[0]) * (rect[3] - rect[1]), 1)


def iou(left: Sequence[int], right: Sequence[int]) -> float:
    ix1, iy1 = max(left[0], right[0]), max(left[1], right[1])
    ix2, iy2 = min(left[2], right[2]), min(left[3], right[3])
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    if not inter:
        return 0.0
    return inter / (_area(left) + _area(right) - inter)


def containment(inner: Sequence[int], outer: Sequence[int]) -> float:
    """Share of the smaller box covered by the intersection (scale mismatch fix)."""

    ix1, iy1 = max(inner[0], outer[0]), max(inner[1], outer[1])
    ix2, iy2 = min(inner[2], outer[2]), min(inner[3], outer[3])
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    if not inter:
        return 0.0
    return inter / min(_area(inner), _area(outer))


# --------------------------------------------------------------------------- #
# Alignment + merge
# --------------------------------------------------------------------------- #


def align(
    vision_elements: Sequence[Mapping[str, Any]],
    a11y_elements: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], set[int], set[int]]:
    """Greedy one-to-one match between vision controls and a11y nodes.

    Vision elements may expose their box as ``rect`` or ``box``; the label is
    read from ``text`` or ``label``. Returns ``(pairs, used_vision, used_a11y)``.
    """

    def vision_rect(item: Mapping[str, Any]) -> list[int] | None:
        return _coerce_rect(item.get("rect") or item.get("box"))

    def vision_label(item: Mapping[str, Any]) -> str:
        return str(item.get("text") or item.get("label") or "")

    scored: list[tuple[float, int, int, float, float, float]] = []
    for vision_index, vision in enumerate(vision_elements):
        v_rect = vision_rect(vision)
        if not v_rect:
            continue
        for a11y_index, node in enumerate(a11y_elements):
            a_rect = node.get("rect")
            if not a_rect:
                continue
            overlap = iou(v_rect, a_rect)
            covered = containment(v_rect, a_rect)
            cx, cy = (v_rect[0] + v_rect[2]) / 2, (v_rect[1] + v_rect[3]) / 2
            acx, acy = (a_rect[0] + a_rect[2]) / 2, (a_rect[1] + a_rect[3]) / 2
            distance = ((cx - acx) ** 2 + (cy - acy) ** 2) ** 0.5
            span = min(v_rect[2] - v_rect[0], v_rect[3] - v_rect[1])
            near = distance <= max(12.0, 0.25 * span)
            if overlap < 0.25 and covered < 0.80 and not near:
                continue
            similarity = text_similarity(vision_label(vision), node.get("name"))
            score = 0.5 * overlap + 0.3 * covered + 0.4 * similarity + (0.1 if near else 0.0)
            scored.append((score, vision_index, a11y_index, overlap, similarity, distance))

    scored.sort(reverse=True)
    used_vision: set[int] = set()
    used_a11y: set[int] = set()
    pairs: list[dict[str, Any]] = []
    for score, vision_index, a11y_index, overlap, similarity, distance in scored:
        if vision_index in used_vision or a11y_index in used_a11y:
            continue
        used_vision.add(vision_index)
        used_a11y.add(a11y_index)
        node = a11y_elements[a11y_index]
        vision = vision_elements[vision_index]
        pairs.append(
            {
                "vision_index": vision_index,
                "a11y_index": a11y_index,
                "vision_text": vision_label(vision),
                "a11y_name": node.get("name") or "",
                "role": node.get("role"),
                "iou": round(overlap, 3),
                "text_similarity": round(similarity, 3),
                "distance_px": round(distance, 1),
                "score": round(score, 3),
                "vision_rect": vision_rect(vision),
                "a11y_rect": node.get("rect"),
                "conflict": bool(vision_label(vision) and node.get("name") and similarity < 0.3),
            }
        )
    return pairs, used_vision, used_a11y


VALUE_ROLES = frozenset(
    {"textbox", "searchbox", "combobox", "spinbutton", "slider", "listbox", "option"}
)


def _viewport_box(a11y: Mapping[str, Any]) -> list[float] | None:
    """Return [0, 0, width, height] when the collector reported a viewport."""

    viewport = a11y.get("viewport")
    width = height = None
    if isinstance(viewport, list | tuple) and len(viewport) >= 2:
        width, height = viewport[0], viewport[1]
    elif isinstance(viewport, Mapping):
        width = viewport.get("width") or viewport.get("clientWidth")
        height = viewport.get("height") or viewport.get("clientHeight")
    try:
        if width and height:
            return [0.0, 0.0, float(width), float(height)]
    except (TypeError, ValueError):
        return None
    return None


def _intersects(rect: Sequence[float], box: Sequence[float]) -> bool:
    return rect[0] < box[2] and rect[2] > box[0] and rect[1] < box[3] and rect[3] > box[1]


def merge(
    vision_elements: Sequence[Mapping[str, Any]],
    a11y: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Merge three sources into one element list plus an alignment report.

    ``vision_elements`` are the already-fused YOLO+OCR elements (dicts with
    ``rect``/``text``/``source``/``clickable``...). Matched elements gain
    ``a11y_role``/``a11y_name``/``a11y_rect`` and an extra ``a11y`` source tag;
    unmatched a11y nodes are appended as their own clickable elements.
    """

    a11y_elements = list(a11y.get("elements") or [])
    bounds = _viewport_box(a11y)
    skipped_offscreen = 0
    skipped_no_rect = 0
    value_role_matches = 0
    pairs, used_vision, used_a11y = align(vision_elements, a11y_elements)

    merged: list[dict[str, Any]] = []
    for element in vision_elements:
        item = dict(element)
        sources = list(item.get("sources") or [item.get("source") or "vision"])
        item["sources"] = sources
        merged.append(item)

    for pair in pairs:
        item = merged[pair["vision_index"]]
        node = a11y_elements[pair["a11y_index"]]
        item["a11y_role"] = node.get("role")
        item["a11y_name"] = node.get("name") or ""
        item["a11y_rect"] = node.get("rect")
        item["a11y_alignment"] = {
            "iou": pair["iou"],
            "text_similarity": pair["text_similarity"],
            "distance_px": pair["distance_px"],
            "conflict": pair["conflict"],
        }
        if "a11y" not in item["sources"]:
            item["sources"].append("a11y")
        role = str(node.get("role") or "")
        if role in VALUE_ROLES:
            # For inputs the a11y name is the label while the OCR text is the
            # value, so they are different layers, not a contradiction.
            value_role_matches += 1
            item["a11y_label"] = item["a11y_name"]
            item["a11y_alignment"]["conflict"] = False
            if not item.get("text"):
                item["text"] = item["a11y_name"]
                item["a11y_filled"] = True
        # A semantic name beats OCR text when the two channels agree, and a
        # whole-button a11y box is a safer click target than a text-only box.
        elif item.get("a11y_name") and (not item.get("text") or pair["text_similarity"] >= 0.6):
            if not item.get("text"):
                item["a11y_filled"] = True
            item["text"] = item["a11y_name"]
        if node.get("rect") and item.get("clickable"):
            item["click"] = [
                round((node["rect"][0] + node["rect"][2]) / 2),
                round((node["rect"][1] + node["rect"][3]) / 2),
            ]
            item["center"] = list(item["click"])

    next_id = max((int(item.get("id") or 0) for item in merged), default=0) + 1
    for index, node in enumerate(a11y_elements):
        if index in used_a11y:
            continue
        rect = node.get("rect")
        if not rect:
            skipped_no_rect += 1
            continue
        if bounds and not _intersects(rect, bounds):
            # Off-screen nodes are still in the tree; appending them would hand
            # the caller coordinates outside the screenshot.
            skipped_offscreen += 1
            continue
        merged.append(
            {
                "id": next_id,
                "text": node.get("name") or "",
                "type": "a11y_only",
                "role": node.get("role"),
                "confidence": 1.0,
                "ocr_confidence": None,
                "clickable": node.get("role") in INTERACTIVE_ROLES,
                "rect": rect,
                "center": [round((rect[0] + rect[2]) / 2), round((rect[1] + rect[3]) / 2)],
                "click": [round((rect[0] + rect[2]) / 2), round((rect[1] + rect[3]) / 2)],
                "source": "a11y",
                "sources": ["a11y"],
                "a11y_role": node.get("role"),
                "a11y_name": node.get("name") or "",
                "a11y_rect": rect,
            }
        )
        next_id += 1

    vision_total = len(vision_elements)
    conflicts = [
        pair
        for pair in pairs
        if pair["conflict"] and a11y_elements[pair["a11y_index"]].get("role") not in VALUE_ROLES
    ]
    report = {
        "source": a11y.get("mode"),
        "node_count": a11y.get("node_count"),
        "interactive_count": len(a11y_elements),
        "visible_interactive": len(a11y_elements) - skipped_offscreen - skipped_no_rect,
        "matched": len(pairs),
        "vision_to_a11y_rate": round(len(used_vision) / vision_total, 3) if vision_total else 0.0,
        "a11y_to_vision_rate": round(len(used_a11y) / len(a11y_elements), 3) if a11y_elements else 0.0,
        "text_conflicts": len(conflicts),
        "value_role_matches": value_role_matches,
        "a11y_only_added": len(merged) - vision_total,
        "skipped_offscreen": skipped_offscreen,
        "skipped_no_rect": skipped_no_rect,
        "semantic_filled": sum(1 for item in merged if item.get("a11y_filled")),
        "conflict_examples": [
            {"vision": pair["vision_text"], "a11y": pair["a11y_name"], "iou": pair["iou"]}
            for pair in conflicts[:5]
        ],
    }
    return merged, report


# --------------------------------------------------------------------------- #
# Probe / verdict
# --------------------------------------------------------------------------- #


def verdict_from_coverage(
    *,
    node_count: int,
    interactive_count: int,
    named_rate: float,
    coord_rate: float,
    coverage: float | None = None,
) -> dict[str, Any]:
    checks = {
        "L1_tree_size": node_count >= MIN_AX_NODES,
        "L2_interactive_coverage": coverage is None or coverage >= MIN_INTERACTIVE_COVERAGE,
        "L3_named_rate": named_rate >= MIN_NAMED_RATE,
        "L4_coord_rate": coord_rate >= MIN_COORD_RATE,
    }
    if node_count < MIN_AX_NODES or interactive_count == 0:
        strategy = "vision_only"
        summary = "No usable accessibility tree (typical for canvas/WebGL or non-instrumented UIs)."
    elif not checks["L2_interactive_coverage"] or not checks["L3_named_rate"]:
        strategy = "hybrid"
        summary = "Accessibility tree is partly usable: keep a11y names, rely on vision for geometry and gaps."
    else:
        strategy = "a11y_primary"
        summary = "Accessibility tree is rich and named; prefer it for semantics and click targets."
    return {"strategy": strategy, "summary": summary, "checks": checks}


def probe(spec: Mapping[str, Any] | str) -> dict[str, Any]:
    """Collect an a11y tree and grade whether it is usable as a control source."""

    collected = collect(spec)
    if collected is None:
        raise A11yError("probe() needs an a11y section with mode='cdp' or 'inline'.", code="a11y_invalid_payload")
    elements = collected["elements"]
    interactive = len(elements)
    named = sum(1 for item in elements if item.get("name"))
    coords = sum(1 for item in elements if item.get("rect"))
    named_rate = named / interactive if interactive else 0.0
    coord_rate = coords / interactive if interactive else 0.0
    verdict = verdict_from_coverage(
        node_count=collected.get("node_count") or interactive,
        interactive_count=interactive,
        named_rate=named_rate,
        coord_rate=coord_rate,
    )
    return {
        **collected,
        "stats": {
            "interactive_count": interactive,
            "named_count": named,
            "named_rate": round(named_rate, 3),
            "coord_count": coords,
            "coord_rate": round(coord_rate, 3),
        },
        "verdict": verdict,
    }


def iter_elements(payload: Iterable[Mapping[str, Any]]) -> Iterable[Mapping[str, Any]]:
    """Small helper for callers that want to stream elements without copying."""

    yield from payload
