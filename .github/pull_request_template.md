## Summary

Describe the user-visible change and why it is needed.

## API impact

- [ ] No API contract change
- [ ] Additive response/form field
- [ ] Breaking change documented in `CHANGELOG.md`

## Verification

Describe how you verified the change. Do not attach private screenshots or secrets.

## Checklist

- [ ] No secrets, model weights, local test content or generated artifacts are included
- [ ] `ruff check src scripts` passes
- [ ] `python -m compileall -q src scripts` passes
- [ ] CPU/GPU and DirectML/CUDA behavior verified or explicitly noted
- [ ] Documentation updated when needed