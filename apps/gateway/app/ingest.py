from __future__ import annotations

import asyncio
import os
import subprocess
from collections.abc import AsyncGenerator

import numpy as np
import yt_dlp


SAMPLE_RATE = 16000


def _clean_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"]:
        env[key] = ""
    return env


class URLAudioSource:
    def __init__(self, url: str, chunk_seconds: float) -> None:
        self.url = url
        self.chunk_bytes = int(SAMPLE_RATE * chunk_seconds * 2)
        self.env = _clean_env()
        self.proc: subprocess.Popen[bytes] | None = None

    def _resolve_url(self) -> str:
        lowered = self.url.lower()
        if lowered.endswith((".m3u8", ".mp4", ".ts", ".webm", ".mp3", ".wav")):
            return self.url
        with yt_dlp.YoutubeDL(
            {"quiet": True, "no_warnings": True, "noplaylist": True, "proxy": "", "format": "bestaudio/best"}
        ) as ydl:
            info = ydl.extract_info(self.url, download=False)
            return str(info.get("url") or info["requested_formats"][0]["url"])

    async def stream(self) -> AsyncGenerator[np.ndarray, None]:
        media_url = await asyncio.to_thread(self._resolve_url)
        self.proc = subprocess.Popen(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-reconnect",
                "1",
                "-reconnect_streamed",
                "1",
                "-reconnect_delay_max",
                "5",
                "-i",
                media_url,
                "-vn",
                "-f",
                "s16le",
                "-ar",
                str(SAMPLE_RATE),
                "-ac",
                "1",
                "pipe:1",
            ],
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            bufsize=0,
        )
        try:
            while True:
                assert self.proc.stdout is not None
                data = await asyncio.to_thread(self.proc.stdout.read, self.chunk_bytes)
                if not data:
                    break
                if len(data) < self.chunk_bytes:
                    data += b"\x00" * (self.chunk_bytes - len(data))
                yield np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
        finally:
            await self.stop()

    async def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                await asyncio.to_thread(self.proc.wait, 3)
            except Exception:
                self.proc.kill()


class VADBuffer:
    def __init__(self, silence_threshold: float, silence_duration: float, min_speech_seconds: float, max_buffer_seconds: float) -> None:
        self.threshold = silence_threshold
        self.silence_frames = int(silence_duration * SAMPLE_RATE)
        self.min_frames = int(min_speech_seconds * SAMPLE_RATE)
        self.max_frames = int(max_buffer_seconds * SAMPLE_RATE)
        self.buffer: list[np.ndarray] = []
        self.buffer_len = 0
        self.silence_len = 0

    def add(self, chunk: np.ndarray) -> np.ndarray | None:
        self.buffer.append(chunk)
        self.buffer_len += len(chunk)
        rms = float(np.sqrt(np.mean(chunk.astype(np.float64) ** 2)))
        self.silence_len = self.silence_len + len(chunk) if rms < self.threshold else 0
        if self.buffer_len >= self.max_frames:
            return self._flush()
        if self.silence_len >= self.silence_frames and self.buffer_len >= self.min_frames:
            return self._flush()
        return None

    def flush(self) -> np.ndarray | None:
        if self.buffer_len >= self.min_frames:
            return self._flush()
        self.reset()
        return None

    def reset(self) -> None:
        self.buffer = []
        self.buffer_len = 0
        self.silence_len = 0

    def _flush(self) -> np.ndarray:
        data = np.concatenate(self.buffer)
        self.reset()
        return data
