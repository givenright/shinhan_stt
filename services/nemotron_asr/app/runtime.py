from __future__ import annotations

import asyncio
import tempfile
import time
import wave
from pathlib import Path

import nemo.collections.asr as nemo_asr
import numpy as np
import torch

from .config import settings


class NemotronRuntime:
    def __init__(self) -> None:
        model_path = Path(settings.model_path)
        if not model_path.exists():
            raise FileNotFoundError(
                f"Nemotron model not found: {model_path}. "
                "Put nemotron-speech-streaming-en-0.6b.nemo under the mounted /models path."
            )
        self.model = nemo_asr.models.ASRModel.restore_from(
            restore_path=settings.model_path,
            map_location=settings.device,
        )
        self.model.eval()

    async def transcribe(self, audio: np.ndarray) -> tuple[str, int]:
        started = time.time()
        text = await asyncio.to_thread(self._transcribe_sync, audio)
        return text, round((time.time() - started) * 1000)

    def _transcribe_sync(self, audio: np.ndarray) -> str:
        pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp:
            temp_path = Path(temp.name)
        try:
            with wave.open(str(temp_path), "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(settings.sample_rate)
                wav_file.writeframes(pcm.tobytes())
            with torch.no_grad():
                result = self.model.transcribe([str(temp_path)], batch_size=1)
            if isinstance(result, list):
                return str(result[0]).strip()
            return str(result).strip()
        finally:
            temp_path.unlink(missing_ok=True)
