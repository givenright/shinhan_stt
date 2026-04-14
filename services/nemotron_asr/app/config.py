from __future__ import annotations

import os


class Settings:
    host = os.getenv("NEMOTRON_HOST", "0.0.0.0")
    port = int(os.getenv("NEMOTRON_PORT", "8090"))
    model_path = os.getenv(
        "NEMOTRON_MODEL_PATH",
        "/models/nvidia/nemotron-speech-streaming-en-0.6b/nemotron-speech-streaming-en-0.6b.nemo",
    )
    device = os.getenv("NEMOTRON_DEVICE", "cuda")
    partial_window_seconds = float(os.getenv("PARTIAL_WINDOW_SECONDS", "1.12"))
    partial_update_interval = float(os.getenv("PARTIAL_UPDATE_INTERVAL", "0.56"))
    sample_rate = 16000


settings = Settings()
