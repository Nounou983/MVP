"""Entry point for the GPU worker.

    python worker.py

Claims queued jobs and forwards them to the model service
(`CIGOGNE_AI_URL`, default http://127.0.0.1:8000). Run one per GPU.
"""
from app.services.worker import main

if __name__ == "__main__":
    main()
