---
title: La Cigogne D'Ailleurs AI — ZeroGPU
emoji: 🛋️
colorFrom: purple
colorTo: gray
sdk: gradio
python_version: "3.12"
---

# La Cigogne D'Ailleurs — ZeroGPU AI validation

This Space reuses the existing `server/main.py` AI implementation and exposes
small Gradio validation workflows for room analysis and remove-only AI.

## Deployment

1. Create a Hugging Face Space with **Gradio** and select **ZeroGPU** hardware.
2. Copy the repository contents into the Space root, keeping `server/` and this
   adapter available at the paths expected by `app.py`.
3. Install the requirements from `requirements.txt`.
4. Verify the Space logs report CUDA/model loading successfully.

ZeroGPU is a shared GPU system. Free personal accounts can host up to two
ZeroGPU Spaces when the account meets Hugging Face's current eligibility
requirements. Free usage has a daily GPU quota, so it is suitable for demos
and validation, not an unlimited client-production backend.

The main product can later call this worker through a thin gateway once the
Space is deployed and its Gradio API contract is verified.
