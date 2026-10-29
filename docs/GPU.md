# GPU acceleration

Label Controllers supports CPU, NVIDIA CUDA and native Windows DirectML. The same image
segmentation, OCR, fusion, annotation, authentication and API payloads are retained across
backends.

## Backend matrix

| Host | YOLO device | OCR provider | Status |
|---|---|---|---|
| Linux + NVIDIA Container Toolkit | CUDA 12.4 (`torch` CUDA image) | `onnxruntime-gpu 1.20.2` / `CUDAExecutionProvider` | Docker image built; GPU execution not available on validation host |
| Windows 10/11 + compatible GPU | DirectML (`torch-directml`) | `DmlExecutionProvider` | Tested on AMD RX 7800 XT |
| CPU-only host/container | CPU | `CPUExecutionProvider` | Baseline |
| Linux + native ROCm | PyTorch ROCm device | `ROCMExecutionProvider` when installed | Experimental |

The current validation host is an AMD Radeon RX 7800 XT under Windows/WSL2. AMD's WSL
GPU path exposes the DirectX paravirtual device (`/dev/dxg`) but not the Linux compute
device (`/dev/kfd`). Therefore Linux Docker cannot use this AMD GPU for ROCm; DirectML in
native Windows is the validated GPU path on this host. NVIDIA CUDA remains the supported
Linux Docker GPU path.

## NVIDIA CUDA with Docker

Requirements:

- NVIDIA GPU with a recent driver supporting CUDA 12.4
- NVIDIA Container Toolkit configured for Docker
- Docker Compose v2.30 or newer because the override uses `gpus: all`
- At least 20 GB free disk space; the CUDA PyTorch base layer downloads at approximately
  3.2 GB compressed and the completed validation image was 12 GB

Start the CPU Compose file and the CUDA override together:

```bash
cp .env.example .env
# Set LABEL_CONTROLLERS_API_KEY in .env first.
docker compose -f compose.yaml -f compose.gpu.yaml up -d --build
docker compose -f compose.yaml -f compose.gpu.yaml ps
curl --fail http://127.0.0.1:8000/readyz
```

The override builds `Dockerfile.cuda`, sets `LABEL_CONTROLLERS_DEVICE=cuda` and
`LABEL_CONTROLLERS_OCR_DEVICE=cuda`, and requests all host GPUs. To expose only one GPU,
change `gpus: all` to `gpus: "device=0"` in `compose.gpu.yaml` and keep
`LABEL_CONTROLLERS_DEVICE_INDEX=0`.

GPU readiness must report:

```json
{
  "models": {
    "icon": {"device": "cuda"},
    "ocr": {"device": "cuda", "provider": "CUDAExecutionProvider"}
  }
}
```

The CUDA image builds successfully on the current host. A read-only, non-root container
smoke test with `LABEL_CONTROLLERS_DEVICE=cpu` passed authenticated JSON inference. The
current machine has no NVIDIA GPU, so `CUDAExecutionProvider` kernel execution and CUDA
latency have not been validated locally. Run the readiness and benchmark checks on the
target NVIDIA deployment before production use.

## Windows DirectML

DirectML is recommended for AMD GPUs that cannot be passed through to Linux Docker.
It runs the FastAPI service natively in a dedicated Windows Python environment and keeps
the same endpoint contract.

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\setup-windows-directml.ps1
.\scripts\run-windows-directml.ps1 -Port 8000
```

The setup script creates `.venv-directml`, installs CPU PyTorch, `torch-directml`,
`onnxruntime-directml`, RapidOCR and the project, then verifies the DirectML adapter.
The run script loads basic key/value pairs from the ignored `.env`, forces device settings
to `directml`, and starts Uvicorn.

Verify the adapter and provider:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/readyz |
  ConvertTo-Json -Depth 8
```

Expected highlights:

```json
{
  "models": {
    "icon": {
      "device": "directml",
      "device_name": "AMD Radeon RX 7800 XT"
    },
    "ocr": {
      "device": "directml",
      "provider": "DmlExecutionProvider"
    }
  }
}
```

### DirectML implementation notes

- YOLO convolutions and model math execute through `torch-directml`.
- Ultralytics 8.3.203 uses inference tensors and a concatenation operator that the current
  DirectML plugin cannot execute in its legacy NMS path. The service keeps model inference
  on GPU and moves only NMS box post-processing to CPU. This does not alter fused output.
- ONNX Runtime's DirectML session must initialize before the first `torch-directml` kernel
  on the validated AMD driver. The engine therefore warms OCR before YOLO at startup.
- OCR stays on the same class of accelerator; it is not silently moved to CPU when
  `LABEL_CONTROLLERS_OCR_DEVICE=directml`.
- Only one inference request executes at a time by design. Increasing Uvicorn workers
  multiplies model memory and does not share GPU sessions.
- `pip check` reports that RapidOCR declares the `onnxruntime` distribution. The DirectML
  setup intentionally installs RapidOCR with `--no-deps` after `onnxruntime-directml` so
  the shared `onnxruntime` Python package is supplied once. This warning is expected; do
  not install the CPU `onnxruntime` wheel over the DirectML runtime.

## Configuration

| Variable | Values | Default | Meaning |
|---|---|---|---|
| `LABEL_CONTROLLERS_DEVICE` | `auto`, `cpu`, `cuda`, `rocm`, `directml`, `mps` | `auto` | YOLO/torch device |
| `LABEL_CONTROLLERS_DEVICE_INDEX` | integer | `0` | GPU index |
| `LABEL_CONTROLLERS_OCR_DEVICE` | `auto`, `cpu`, `cuda`, `rocm`, `directml` | `auto` | ONNX Runtime execution provider |

`auto` prefers CUDA/ROCm, then Apple MPS, then DirectML, then CPU for torch. OCR chooses
the matching GPU provider when the installed ONNX Runtime exposes it, otherwise CPU.

Runtime information is available from:

- `GET /readyz` under `models.runtime`
- `GET /api/v1/capabilities` under `compute`
- `metadata.runtime` in analysis responses
- `X-Model-Device` and `X-OCR-Provider` response headers

## CPU fallback

The default Docker path remains CPU-only:

```bash
docker compose up -d --build
```

The Compose file explicitly sets `LABEL_CONTROLLERS_DEVICE=cpu` and
`LABEL_CONTROLLERS_OCR_DEVICE=cpu`, so installing GPU libraries on the host does not
change the default container behavior.

## Further reading

See [BENCHMARKS.md](BENCHMARKS.md) for the measured RX 7800 XT DirectML comparison and the
reproducible benchmark commands.
