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

The container listens on `0.0.0.0:8000`. Compose publishes it on
`${LABEL_CONTROLLERS_API_PORT:-8000}`. To expose it beyond the local machine,
use a firewall and an HTTPS reverse proxy such as Caddy, Nginx or Traefik:

```text
client -> HTTPS reverse proxy -> 127.0.0.1:8000 -> label-controllers container
```

Do not publish the plain HTTP port directly to the public Internet.

## Container properties

- CPU-only inference; no GPU runtime required.
- Runs as non-root UID/GID `10001`.
- Read-only root filesystem, with writable `/tmp` tmpfs and `/data` volume.
- Linux capabilities are dropped.
- `/healthz` is used by the image and Compose health checks.
- Models are loaded once at process startup.

## Initial build

The Dockerfile uses a multi-stage build:

1. Download and SHA-256 verify `icon_detect.pt`.
2. Install pinned CPU dependencies and system fonts.
3. Copy the verified model into the runtime image.
4. Drop privileges and start Uvicorn.

The first build downloads several hundred MB of packages and may take time. Subsequent
builds reuse cached layers unless `requirements.txt` or the model downloader changes.

## Resource sizing

The service is CPU-bound during model inference. For a small team, start with:

- 4 CPU cores
- 4 GB RAM
- 2 GB free disk
- One Uvicorn worker

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

The repository is prepared but intentionally has no remote or credential committed.
When the GitHub account/secret is supplied, add the remote and push:

```bash
git remote add origin https://github.com/<owner>/label-controllers.git
git push -u origin main
```

Prefer a credential manager, GitHub CLI login, or a short-lived token supplied through
the environment. Never commit a PAT, SSH private key or `.env` file.

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