from __future__ import annotations

import os


def _float(name: str, default: float) -> float:
    value = os.getenv(name)
    return float(value) if value is not None else default


class Settings:
    host = os.getenv("GATEWAY_HOST", "0.0.0.0")
    port = int(os.getenv("GATEWAY_PORT", "8080"))
    stt_ws_url = os.getenv("STT_WS_URL", "ws://nemotron-asr:8090/ws/transcribe")
    translation_backend = os.getenv("TRANSLATION_BACKEND", "vllm").lower()
    vllm_base_url = os.getenv("VLLM_BASE_URL", "http://vllm.internal:8000")
    vllm_chat_path = os.getenv("VLLM_CHAT_PATH", "/v1/chat/completions")
    vllm_model_name = os.getenv("VLLM_MODEL_NAME", "gpt-4o")
    vllm_timeout = _float("VLLM_TIMEOUT", 10.0)
    google_translate_url = os.getenv(
        "GOOGLE_TRANSLATE_URL",
        "https://translate.googleapis.com/translate_a/single",
    )
    ingress_chunk_seconds = _float("INGRESS_CHUNK_SECONDS", 0.16)
    vad_silence_threshold = _float("VAD_SILENCE_THRESHOLD", 0.015)
    vad_silence_duration = _float("VAD_SILENCE_DURATION", 0.6)
    vad_min_speech_seconds = _float("VAD_MIN_SPEECH_SECONDS", 0.6)
    vad_max_buffer_seconds = _float("VAD_MAX_BUFFER_SECONDS", 12.0)


settings = Settings()
