from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np

_OCR_PROVIDERS: list[str] = ["CPUExecutionProvider"]
_ACTIVATED_OCR_PROVIDERS: set[str] = set()
_RAPIDOCR_OVERRIDE_INSTALLED = False
_DIRECTML_COMPATIBILITY_INSTALLED = False

OCR_CPU_REC_BATCH_NUM = 6
OCR_GPU_REC_BATCH_NUM = 12


def default_ocr_rec_batch_num(provider: str) -> int:
    """Use larger recognition batches only where they help.

    DirectML tests showed a clear throughput gain at batch 12, while larger
    batches slowed down CPU inference substantially. Keep the conservative
    RapidOCR default for CPU and use the measured GPU default elsewhere.
    """

    return OCR_GPU_REC_BATCH_NUM if provider != "CPUExecutionProvider" else OCR_CPU_REC_BATCH_NUM


def _install_directml_compatibility() -> None:
    """Apply the two compatibility shims required by torch-directml 0.2.5.

    torch-directml 0.2.5 targets PyTorch 2.4.1. Two upstream paths assume full
    CUDA semantics: inference tensors cannot be changed in place in Ultralytics'
    legacy NMS, and the NMS concatenation is not implemented by DirectML. We
    keep all convolutions and model math on the GPU, then run only box
    post-processing on the CPU. This is deterministic and preserves outputs.
    """

    global _DIRECTML_COMPATIBILITY_INSTALLED
    if _DIRECTML_COMPATIBILITY_INSTALLED:
        return

    import torch

    class _NullInferenceContext:
        def __enter__(self) -> None:
            return None

        def __exit__(self, *_args: object) -> bool:
            return False

        def __call__(self, function: Any) -> Any:
            return function

    def _null_inference_mode(mode: Any = True) -> Any:
        if callable(mode):
            return mode()
        return _NullInferenceContext()

    torch.inference_mode = _null_inference_mode

    import ultralytics.utils.nms as nms_module

    original_nms = nms_module.non_max_suppression

    def _cpu_nms(prediction: Any, *args: Any, **kwargs: Any) -> Any:
        if isinstance(prediction, list | tuple):
            prediction = [item.cpu().clone() if torch.is_tensor(item) else item for item in prediction]
        elif torch.is_tensor(prediction):
            prediction = prediction.cpu().clone()
        return original_nms(prediction, *args, **kwargs)

    nms_module.non_max_suppression = _cpu_nms
    _DIRECTML_COMPATIBILITY_INSTALLED = True


class IconDetector:
    def __init__(self, weights: str | Path, device: Any = "cpu", backend: str = "cpu"):
        self.weights = str(weights)
        self.device = device
        self.backend = backend
        if backend == "directml":
            _install_directml_compatibility()
        from ultralytics import YOLO

        self.model = YOLO(self.weights)

    def warmup(self, imgsz: int = 640) -> float:
        """Compile kernels and allocate buffers before serving traffic."""

        started = time.perf_counter()
        sample = np.zeros((max(320, min(imgsz, 640)), max(320, min(imgsz, 640)), 3), dtype=np.uint8)
        self.model.predict(
            source=sample,
            imgsz=max(320, min(imgsz, 640)),
            conf=0.99,
            iou=0.55,
            device=self.device,
            verbose=False,
        )
        return time.perf_counter() - started

    def predict(
        self, image_path: str | Path, imgsz: int = 1280, conf: float = 0.20
    ) -> tuple[list[dict[str, Any]], float]:
        started = time.perf_counter()
        results = self.model.predict(
            source=str(image_path),
            imgsz=imgsz,
            conf=conf,
            iou=0.55,
            device=self.device,
            verbose=False,
        )
        elapsed = time.perf_counter() - started
        result = results[0]
        boxes = result.boxes
        detections: list[dict[str, Any]] = []
        if boxes is None:
            return detections, elapsed
        xyxy = boxes.xyxy.cpu().numpy() if boxes.xyxy is not None else np.empty((0, 4))
        scores = boxes.conf.cpu().numpy() if boxes.conf is not None else np.empty((0,))
        classes = boxes.cls.cpu().numpy().astype(int) if boxes.cls is not None else np.empty((0,), dtype=int)
        for index, (box, score, cls) in enumerate(zip(xyxy, scores, classes, strict=False)):
            detections.append(
                {
                    "index": index,
                    "box": [int(round(float(v))) for v in box.tolist()],
                    "conf": round(float(score), 4),
                    "class_id": int(cls),
                    "class_name": str(result.names.get(int(cls), cls)),
                }
            )
        return detections, elapsed


def _install_rapidocr_provider_override() -> None:
    """Teach RapidOCR 1.2.3 to honor a configurable ONNX Runtime provider."""

    global _RAPIDOCR_OVERRIDE_INSTALLED
    if _RAPIDOCR_OVERRIDE_INSTALLED:
        return

    import onnxruntime as ort
    import rapidocr_onnxruntime.utils as rapidocr_utils
    from onnxruntime import GraphOptimizationLevel, SessionOptions

    original_session = rapidocr_utils.OrtInferSession

    class ConfiguredOrtInferSession(original_session):  # type: ignore[misc, valid-type]
        def __init__(self, config: dict[str, Any]):
            sess_options = SessionOptions()
            sess_options.log_severity_level = 4
            sess_options.enable_cpu_mem_arena = False
            sess_options.graph_optimization_level = GraphOptimizationLevel.ORT_ENABLE_ALL

            provider = _OCR_PROVIDERS[0]
            providers: list[str | tuple[str, dict[str, Any]]]
            if provider == "DmlExecutionProvider":
                providers = [(provider, {"device_id": 0}), "CPUExecutionProvider"]
            elif provider == "CUDAExecutionProvider":
                providers = [
                    (
                        provider,
                        {
                            "device_id": 0,
                            "arena_extend_strategy": "kNextPowerOfTwo",
                            "cudnn_conv_algo_search": "EXHAUSTIVE",
                            "do_copy_in_default_stream": True,
                        },
                    ),
                    "CPUExecutionProvider",
                ]
            elif provider == "ROCRuntimeExecutionProvider":
                providers = [(provider, {"device_id": 0}), "CPUExecutionProvider"]
            else:
                providers = [("CPUExecutionProvider", {"arena_extend_strategy": "kSameAsRequested"})]

            self._verify_model(config["model_path"])
            self.session = ort.InferenceSession(
                config["model_path"],
                sess_options=sess_options,
                providers=providers,
            )
            actual_providers = self.session.get_providers()
            if actual_providers:
                _ACTIVATED_OCR_PROVIDERS.add(actual_providers[0])

        def __call__(self, input_content: np.ndarray) -> Any:
            # Preserve the original ONNX Runtime exception so startup diagnostics
            # identify unsupported operators or providers instead of a generic error.
            input_names = self.get_input_names()
            return self.session.run(self.get_output_names(), dict(zip(input_names, [input_content], strict=False)))

        def get_providers(self) -> list[str]:
            return list(self.session.get_providers())

    rapidocr_utils.OrtInferSession = ConfiguredOrtInferSession
    # Components loaded by an earlier RapidOCR instance may already hold a
    # direct reference to the original class.
    for module_name, module in list(sys.modules.items()):
        if module_name.startswith("rapidocr_onnxruntime.") and module is not rapidocr_utils:
            if getattr(module, "OrtInferSession", None) is original_session:
                module.OrtInferSession = ConfiguredOrtInferSession
    _RAPIDOCR_OVERRIDE_INSTALLED = True


class TextDetector:
    def __init__(
        self,
        provider: str = "CPUExecutionProvider",
        rec_batch_num: int | None = None,
        use_angle_cls: bool = False,
    ):
        global _OCR_PROVIDERS
        _OCR_PROVIDERS = [provider]
        _ACTIVATED_OCR_PROVIDERS.clear()
        _install_rapidocr_provider_override()
        from rapidocr_onnxruntime import RapidOCR

        self.provider = provider
        self.rec_batch_num = (
            rec_batch_num if rec_batch_num is not None and rec_batch_num > 0 else default_ocr_rec_batch_num(provider)
        )
        self.use_angle_cls = use_angle_cls
        # RapidOCR 1.2.3 requires model_path to accompany other Rec overrides.
        # Passing None keeps its packaged PP-OCR model while changing batching.
        self.engine = RapidOCR(
            use_angle_cls=self.use_angle_cls,
            rec_batch_num=self.rec_batch_num,
            rec_model_path=None,
        )
        self.session_providers = sorted(_ACTIVATED_OCR_PROVIDERS)
        if provider not in self.session_providers:
            raise RuntimeError(
                f"ONNX Runtime could not activate {provider}. "
                f"Active providers: {', '.join(self.session_providers) or 'none'}."
            )

    def warmup(self) -> float:
        """Exercise detection/recognition, plus classification only when enabled."""

        started = time.perf_counter()
        canvas = np.full((720, 1280, 3), 255, dtype=np.uint8)
        # OpenCV's built-in font gives the detector a real text line, which also
        # initializes recognition and, when enabled, angle-classification sessions.
        cv2.putText(
            canvas,
            "Label Controllers 12345",
            (100, 360),
            cv2.FONT_HERSHEY_SIMPLEX,
            2.5,
            (0, 0, 0),
            5,
            cv2.LINE_AA,
        )
        self.engine(canvas, use_det=True, use_cls=self.use_angle_cls, use_rec=True)
        return time.perf_counter() - started

    def predict(
        self,
        image: str | Path | np.ndarray,
        min_confidence: float = 0.40,
    ) -> tuple[list[dict[str, Any]], float]:
        started = time.perf_counter()
        result, _ = self.engine(image, use_det=True, use_cls=self.use_angle_cls, use_rec=True)
        elapsed = time.perf_counter() - started
        detections: list[dict[str, Any]] = []
        for index, row in enumerate(result or []):
            if len(row) < 3:
                continue
            quad, text, score = row[0], str(row[1]), float(row[2])
            if score < min_confidence or not text.strip():
                continue
            points = np.asarray(quad, dtype=float)
            x1, y1 = points.min(axis=0).tolist()
            x2, y2 = points.max(axis=0).tolist()
            detections.append(
                {
                    "index": index,
                    "box": [int(round(x1)), int(round(y1)), int(round(x2)), int(round(y2))],
                    "quad": [[round(float(x), 2), round(float(y), 2)] for x, y in points.tolist()],
                    "text": text.strip(),
                    "conf": round(score, 4),
                }
            )
        return detections, elapsed
