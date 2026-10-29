# Deployment

## Docker Compose (recommended)

1. Create a local secret file:

   ```bash
   cp .env.example .env
   ```

2. Generate an API key and place it in `.env`:

   ```bash
   openssl rand -hex 32
   ```

3. Start the service:

   ```bash
   docker compose up -d --build
   docker compose ps
   docker compose logs -f api
   ```

4. Verify readiness:

   ```bash
   curl --fail http://127.0.0.1:8000/readyz
   ```

## NVIDIA GPU Compose

The default image is CPU-only. To build and run the CUDA image, add the GPU override:

```bash
docker compose -f compose.yaml -f compose.gpu.yaml up -d --build
docker compose -f compose.yaml -f compose.gpu.yaml ps
curl --fail http://127.0.0.1:8000/readyz
```

This requires the NVIDIA Container Toolkit and a driver compatible with CUDA 12.4.
The CUDA image has been built and CPU-fallback smoke-tested on the validation host, but its
GPU execution provider requires an NVIDIA host for final validation.
The override uses `gpus: all`, sets both torch and OCR devices to `cuda`, and builds
`Dockerfile.cuda`. See [GPU.md](GPU.md) for DirectML, device pinning and validation details.

The container listens on `0.0.0.0:8000`. Compose publishes it on
`${LABEL_CONTROLLERS_API_PORT:-8000}`. To expose it beyond the local machine,
use a firewall and an HTTPS reverse proxy such as Caddy, Nginx or Traefik:

```text
client -> HTTPS reverse proxy -> 127.0.0.1:8000 -> label-controllers container
```

Do not publish the plain HTTP port directly to the public Internet.

## Container properties

- The default image is CPU-only and requires no GPU runtime.
- The optional CUDA image uses PyTorch CUDA 12.4 and ONNX Runtime CUDA.
- Native Windows DirectML is supported by a separate launcher for AMD GPUs.
- Containers run as non-root UID/GID `10001`.
- Read-only root filesystem, with writable `/tmp` tmpfs and `/data` volume.
- Linux capabilities are dropped.
- `/healthz` is used by the image and Compose health checks.
- Models are loaded once at process startup.

## Initial build

The Dockerfile uses a multi-stage build:

1. Download and SHA-256 verify `icon_detect.pt`.
2. Install pinned CPU or CUDA dependencies and system fonts.
3. Copy the verified model into the runtime image.
4. Drop privileges and start Uvicorn.

The first CPU build downloads several hundred MB of packages. The CUDA build pulls an
approximately 3.2 GB compressed PyTorch base layer that expands to approximately 9.4 GB;
reserve at least 20 GB of free disk. Subsequent builds reuse cached layers unless the
requirements or model downloader changes.

## Resource sizing

CPU inference is CPU-bound. For a small team, start with:

- 4 CPU cores
- 4 GB RAM
- 2 GB free disk
- One Uvicorn worker

For the validated DirectML path, allocate at least 2 GB free GPU memory in addition to the
host RAM above. The GPU benchmark reached 0.77 s mean total latency for the default JSON
response on an RX 7800 XT. NVIDIA CUDA resource use depends on driver, GPU and model input
size; measure the target host with `scripts/benchmark.py`.

The engine serializes inference within a container. For parallel throughput, run multiple
containers behind a queue or reverse proxy and give each one enough memory for its own copy
of both models.

## Operations

```bash
docker compose logs -f api
docker compose restart api
docker compose down
docker compose up -d --build
```

Update an existing deployment:

```bash
git pull
docker compose build --pull
docker compose up -d
```

## GitHub Container Registry

`.github/workflows/docker-publish.yml` publishes `ghcr.io/<owner>/label-controllers` on
pushes to `main`, version tags, and manual runs. It uses the repository-scoped
`GITHUB_TOKEN`; no personal Docker credential is stored in the repository.

## GitHub remote credentials

The remote URL is stored without credentials:

```text
https://github.com/Amywlp/label-controllers.git
```

Use a credential manager, GitHub CLI login, or a short-lived token supplied only through
the environment. Never commit a PAT, SSH private key or `.env` file. Tokens previously used
for repository administration should be revoked after the operation.

## Windows + Docker Engine inside WSL2

Windows' WSL localhost forwarding exposes the published container port on
`127.0.0.1`, but not necessarily on the physical LAN. To publish port `8000`
through Windows, run an elevated PowerShell after Compose is healthy:

```powershell
.\scripts\expose-windows.ps1 -Distro Ubuntu-22.04 -ListenPort 8000
```

The script discovers the current WSL IPv4 address, updates the Windows
`portproxy` rule and creates an inbound firewall rule. WSL can receive a new
address after a reboot, so rerun the script whenever that happens.