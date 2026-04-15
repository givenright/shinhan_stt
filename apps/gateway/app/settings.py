from __future__ import annotations

import os


def _float(name: str, default: float) -> float:
    value = os.getenv(name)
    return float(value) if value is not None else default


class Settings:
    host = os.getenv("GATEWAY_HOST", "0.0.0.0")
    port = int(os.getenv("PORT", os.getenv("GATEWAY_PORT", "8080")))

    assemblyai_api_key = os.getenv("ASSEMBLYAI_API_KEY", "")
    assemblyai_streaming_url = os.getenv("ASSEMBLYAI_STREAMING_URL", "wss://streaming.assemblyai.com/v3/ws")
    assemblyai_sample_rate = int(os.getenv("ASSEMBLYAI_SAMPLE_RATE", "16000"))
    assemblyai_speech_model = os.getenv("ASSEMBLYAI_STREAMING_MODEL", "u3-rt-pro")
    assemblyai_min_turn_silence = int(os.getenv("ASSEMBLYAI_MIN_TURN_SILENCE_MS", "450"))
    assemblyai_max_turn_silence = int(os.getenv("ASSEMBLYAI_MAX_TURN_SILENCE_MS", "900"))
    assemblyai_format_turns = os.getenv("ASSEMBLYAI_FORMAT_TURNS", "true").lower() == "true"

    openai_api_key = os.getenv("OPENAI_API_KEY", "")
    openai_model = os.getenv("OPENAI_MODEL", "gpt-5.3")
    openai_timeout = _float("OPENAI_TIMEOUT", 20.0)
    translation_context_size = int(os.getenv("TRANSLATION_CONTEXT_SIZE", "4"))
    partial_translation_delay = _float("PARTIAL_TRANSLATION_DELAY", 0.35)
    final_punctuation_delay = _float("FINAL_PUNCTUATION_DELAY", 0.85)

    ingress_chunk_seconds = _float("INGRESS_CHUNK_SECONDS", 0.16)
    ytdlp_proxy_url = os.getenv("YTDLP_PROXY_URL", "")
    ytdlp_cookies = os.getenv("YTDLP_COOKIES", "")
    ytdlp_cookies_b64 = os.getenv("YTDLP_COOKIES_B64", "")
    ytdlp_cookies_file = os.getenv("YTDLP_COOKIES_FILE", "")
    ytdlp_impersonate = os.getenv("YTDLP_IMPERSONATE", "chrome")


settings = Settings()
