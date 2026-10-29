# Contributing

Thanks for improving Label Controllers.

## Development setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install --no-deps -e .
pip install -e ".[dev]"
python scripts/download_model.py
```

## Pull requests

- Keep changes focused and explain the user-visible behavior.
- Do not add model weights, screenshots, secrets, local test output or generated artifacts.
- Update `README.md`, `docs/API.md`, `docs/GPU.md` or `docs/TOOL.md` when a contract changes.
- Preserve the public JSON fields unless a versioned breaking change is intentional.
- Run `ruff check src scripts examples` and `python -m compileall -q src scripts examples` before pushing.
- Include a short manual test note in the pull request template.

## API compatibility

The current API is versioned under `/api/v1`. Add fields compatibly where possible.
When a breaking response change is required, document it in `CHANGELOG.md` and discuss
a new API version before changing field meaning or removing fields.

## Security

Never paste API keys, personal screenshots or secrets into an issue or pull request.
Follow [SECURITY.md](SECURITY.md) for private vulnerability reporting.