"""Entry point for the API service (no GPU, no model weights).

    uvicorn api:app --reload --port 8100

This process owns accounts, projects, storage, the catalogue and the job
queue. Model work is handed to `worker.py`, which is the only thing that needs
a card.
"""
from __future__ import annotations

import logging

from app.api import create_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
)

app = create_app()
