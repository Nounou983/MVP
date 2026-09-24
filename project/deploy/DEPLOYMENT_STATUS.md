# Cloud deployment status

## Prepared in this release

- Static frontend remains deployable without a build step.
- API base URL is centralized in `js/config.js`.
- AI gateway/queue architecture is preserved.
- Hugging Face ZeroGPU validation adapter is included under `deploy/huggingface-zerogpu/`.
- ZeroGPU adapter reuses `server/main.py` instead of creating a second AI implementation.

## Important limitation

A real cloud GPU execution cannot be truthfully marked as validated from this
local build environment. The repository does not contain the user's Hugging
Face credentials and this environment does not provide the remote ZeroGPU
runtime. The included adapter is designed for the final cloud execution test.

For the client-facing production system, use a deployed API + persistent DB +
persistent object storage + GPU worker. ZeroGPU's free quota is appropriate for
short demos, not unlimited multi-client production traffic.
