# Security Policy

## Supported versions

Security fixes are applied to the latest version on the default branch.

## Reporting a vulnerability

Please do not open a public issue for a suspected vulnerability. Report it privately to
the repository maintainer with:

- affected version or commit,
- reproduction steps with sensitive values removed,
- impact assessment,
- a minimal proof of concept if safe to share.

Do not include real API keys, personal screenshots, credentials or production captures.
The maintainer should acknowledge a report within a reasonable period and coordinate
disclosure after a fix is available.

## Deployment guidance

- Set `LABEL_CONTROLLERS_API_KEY` for every non-local deployment.
- Put the service behind HTTPS.
- Keep `.env`, TLS private keys, tokens and local screenshots out of Git.
- Apply upload and image-pixel limits.
- Run the container as the supplied non-root user.
- Review logs before sharing them; they may contain request IDs and network metadata.

## Secrets

The repository ships only `.env.example` with a placeholder. `.gitignore` excludes
environment files, private keys, certificates, cloud credentials, model weights,
local test content and generated artifacts. If a secret is committed accidentally,
rotate it immediately; removing it from the latest commit alone is not sufficient.