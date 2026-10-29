"""Runtime accelerator discovery and ONNX Runtime provider selection."""

from __future__ import annotations

import platform
import sys
from dataclasses import dataclass, field
from typing import Any

TORCH_DEVICE_CHOICES = {"auto", "cpu", "cuda", "rocm", "directml", "mps"}
OCR_DEVICE_CHOICES = {"auto", "cpu", "cuda", "rocm", "directml"}


@dataclass(frozen=True, slots=True)
class RuntimeDevices:
    """Validated compute devices shared by the detector and OCR engine."""

    requested_device: str
    backend: str
    device_name: str
    device_index: int
    torch_device: Any = field(repr=False, compare=False)
    torch_version: str
    accelerator_version: str
    ocr_provider: str
    ocr_backend: str

    @property
    def is_gpu(self) -> bool:
        return self.backend != "cpu"

    def as_dict(self) -> dict[str, Any]:
        return {
            "requested_device": self.requested_device,
            "backend": self.backend,
            "device_name": self.device_name,
            "device_index": self.device_index,
            "is_gpu": self.is_gpu,
            "torch_version": self.torch_version,
            "accelerator_version": self.accelerator_version,
            "ocr": {
                "backend": self.ocr_backend,
                "provider": self.ocr_provider,
            },
        }


def _validate_device(torch_device: Any) -> None:
    import torch

    tensor = torch.ones((2, 2), dtype=torch.float32, device=torch_device)
    tensor = tensor + tensor
    # Synchronize CUDA/MPS so driver or kernel failures surface during startup.
    device_type = str(tensor.device).split(":", 1)[0]
    if device_type == "cuda":
        torch.cuda.synchronize()
    elif device_type == "mps":
        _sync_mps(torch)
    del tensor


def _sync_mps(torch: Any) -> None:
    if hasattr(torch, "mps") and hasattr(torch.mps, "synchronize"):
        torch.mps.synchronize()


def _cuda_runtime(requested: str, device_index: int) -> RuntimeDevices:
    import torch

    if not torch.cuda.is_available():
        raise RuntimeError(
            f"{requested.upper()} was requested, but this PyTorch build cannot access a CUDA/ROCm device."
        )
    if device_index >= torch.cuda.device_count():
        raise RuntimeError(
            f"Device index {device_index} is unavailable; detected {torch.cuda.device_count()} CUDA/ROCm device(s)."
        )
    device = f"cuda:{device_index}"
    _validate_device(device)
    is_rocm = bool(getattr(torch.version, "hip", None))
    name = torch.cuda.get_device_name(device_index).strip()
    return RuntimeDevices(
        requested_device=requested,
        backend="rocm" if is_rocm else "cuda",
        device_name=name,
        device_index=device_index,
        torch_device=device,
        torch_version=torch.__version__,
        accelerator_version=str(torch.version.hip or torch.version.cuda or "unknown"),
        ocr_provider="",
        ocr_backend="",
    )


def _directml_runtime(requested: str, device_index: int) -> RuntimeDevices:
    import torch

    if sys.platform != "win32":
        raise RuntimeError("DirectML is only available in native Windows Python environments.")
    try:
        import torch_directml
    except ImportError as exc:
        raise RuntimeError("DirectML was requested, but torch-directml is not installed.") from exc
    except Exception as exc:
        raise RuntimeError("torch-directml could not initialize the DirectML runtime.") from exc
    try:
        count = int(torch_directml.device_count())
        if count <= 0:
            raise RuntimeError("No compatible DirectML GPU was found.")
        if device_index >= count:
            raise RuntimeError(f"Device index {device_index} is unavailable; detected {count} DirectML device(s).")
        device = torch_directml.device(device_index)
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError("torch-directml could not enumerate compatible GPUs.") from exc
    # Do not execute a torch-directml kernel here. On Windows, ONNX Runtime's
    # DirectML session must claim the adapter before torch-directml's first
    # kernel; IconDetector.warmup() performs the actual validation later.
    name = str(torch_directml.device_name(device_index)).replace("\x00", "").strip()
    try:
        from importlib.metadata import version

        torch_directml_version = version("torch-directml")
    except Exception:
        torch_directml_version = "unknown"
    return RuntimeDevices(
        requested_device=requested,
        backend="directml",
        device_name=name,
        device_index=device_index,
        torch_device=device,
        torch_version=torch.__version__,
        accelerator_version=torch_directml_version,
        ocr_provider="",
        ocr_backend="",
    )


def _mps_runtime(requested: str, device_index: int) -> RuntimeDevices:
    import torch

    if not (hasattr(torch.backends, "mps") and torch.backends.mps.is_available()):
        raise RuntimeError("MPS was requested, but this PyTorch build cannot access an Apple GPU.")
    if device_index != 0:
        raise RuntimeError("MPS supports device index 0 only.")
    _validate_device("mps")
    return RuntimeDevices(
        requested_device=requested,
        backend="mps",
        device_name="Apple Metal GPU",
        device_index=0,
        torch_device="mps",
        torch_version=torch.__version__,
        accelerator_version="mps",
        ocr_provider="",
        ocr_backend="",
    )


def _cpu_runtime(requested: str, device_index: int) -> RuntimeDevices:
    import torch

    if device_index != 0:
        raise RuntimeError("CPU supports device index 0 only.")
    return RuntimeDevices(
        requested_device=requested,
        backend="cpu",
        device_name=platform.processor().strip() or "CPU",
        device_index=0,
        torch_device="cpu",
        torch_version=torch.__version__,
        accelerator_version="cpu",
        ocr_provider="",
        ocr_backend="",
    )


def _resolve_torch_runtime(requested_device: str, device_index: int) -> RuntimeDevices:
    requested = requested_device.strip().casefold()
    if requested not in TORCH_DEVICE_CHOICES:
        choices = ", ".join(sorted(TORCH_DEVICE_CHOICES))
        raise RuntimeError(f"Unsupported device '{requested_device}'. Choose from: {choices}.")

    if requested == "cpu":
        return _cpu_runtime(requested, device_index)
    if requested in {"cuda", "rocm"}:
        return _cuda_runtime(requested, device_index)
    if requested == "directml":
        return _directml_runtime(requested, device_index)
    if requested == "mps":
        return _mps_runtime(requested, device_index)

    # Automatic selection prefers native CUDA/ROCm, followed by Apple MPS and DirectML.
    try:
        return _cuda_runtime(requested, device_index)
    except RuntimeError:
        pass
    try:
        return _mps_runtime(requested, device_index)
    except RuntimeError:
        pass
    try:
        return _directml_runtime(requested, device_index)
    except RuntimeError:
        return _cpu_runtime(requested, device_index)


def _ocr_backend_for_provider(provider: str) -> str:
    if provider == "DmlExecutionProvider":
        return "directml"
    if provider == "CUDAExecutionProvider":
        return "cuda"
    if provider in {"ROCMExecutionProvider", "MIGraphXExecutionProvider"}:
        return "rocm"
    return "cpu"


def resolve_ocr_provider(requested_device: str, requested_ocr_device: str) -> tuple[str, str]:
    """Resolve ONNX Runtime without touching the torch-directml runtime.

    On current Windows AMD drivers, ONNX Runtime's DirectML session must claim
    the adapter before torch-directml executes any kernel. The engine therefore
    resolves and warms OCR first, then initializes torch.
    """

    try:
        import onnxruntime as ort
    except ImportError as exc:
        raise RuntimeError("onnxruntime is required for OCR inference.") from exc

    available = set(ort.get_available_providers())
    requested = requested_ocr_device.strip().casefold()
    if requested not in OCR_DEVICE_CHOICES:
        raise RuntimeError(
            f"Unsupported OCR device '{requested_ocr_device}'. "
            f"Choose from: {', '.join(sorted(OCR_DEVICE_CHOICES))}."
        )

    preferred: str | None = None
    if requested == "cpu":
        preferred = "CPUExecutionProvider"
    elif requested == "cuda":
        preferred = "CUDAExecutionProvider"
    elif requested == "rocm":
        preferred = next(
            (name for name in ("ROCMExecutionProvider", "MIGraphXExecutionProvider") if name in available),
            "ROCMExecutionProvider",
        )
    elif requested == "directml":
        preferred = "DmlExecutionProvider"
    else:
        torch_request = requested_device.strip().casefold()
        if torch_request == "directml":
            preferred = "DmlExecutionProvider"
        elif torch_request in {"cuda", "rocm"}:
            preferred = "CUDAExecutionProvider" if torch_request == "cuda" else "ROCMExecutionProvider"
        elif torch_request == "cpu":
            preferred = "CPUExecutionProvider"
        elif "CUDAExecutionProvider" in available:
            preferred = "CUDAExecutionProvider"
        elif sys.platform == "win32" and "DmlExecutionProvider" in available:
            preferred = "DmlExecutionProvider"
        else:
            preferred = "CPUExecutionProvider"

    if preferred and preferred in available:
        return preferred, _ocr_backend_for_provider(preferred)
    if requested != "auto":
        raise RuntimeError(
            f"OCR device '{requested}' requires {preferred}, but the installed ONNX Runtime provides: "
            f"{', '.join(sorted(available)) or 'none'}."
        )
    if "CPUExecutionProvider" in available:
        return "CPUExecutionProvider", "cpu"
    if available:
        provider = sorted(available)[0]
        return provider, _ocr_backend_for_provider(provider)
    raise RuntimeError("ONNX Runtime reports no execution providers.")


def resolve_runtime_devices(
    requested_device: str = "auto",
    requested_ocr_device: str = "auto",
    device_index: int = 0,
    *,
    ocr_provider: str | None = None,
) -> RuntimeDevices:
    """Resolve and validate the PyTorch and ONNX Runtime devices used by the service."""

    if ocr_provider is None:
        provider, ocr_backend = resolve_ocr_provider(requested_device, requested_ocr_device)
    else:
        try:
            import onnxruntime as ort
        except ImportError as exc:
            raise RuntimeError("onnxruntime is required for OCR inference.") from exc
        if ocr_provider not in set(ort.get_available_providers()):
            raise RuntimeError(f"OCR provider '{ocr_provider}' is not available in this ONNX Runtime build.")
        provider, ocr_backend = ocr_provider, _ocr_backend_for_provider(ocr_provider)
    runtime = _resolve_torch_runtime(requested_device, device_index)
    return RuntimeDevices(
        requested_device=runtime.requested_device,
        backend=runtime.backend,
        device_name=runtime.device_name,
        device_index=runtime.device_index,
        torch_device=runtime.torch_device,
        torch_version=runtime.torch_version,
        accelerator_version=runtime.accelerator_version,
        ocr_provider=provider,
        ocr_backend=ocr_backend,
    )
