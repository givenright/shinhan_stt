from __future__ import annotations

import asyncio
import base64
import os
import subprocess
import tempfile
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import yt_dlp

from .settings import settings


def _clean_env() -> dict[str, str]:
    env = os.environ.copy()
    if not settings.ytdlp_proxy_url:
        for key in ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"]:
            env[key] = ""
    return env


class URLAudioSource:
    def __init__(self, url: str, chunk_seconds: float) -> None:
        self.url = url
        self.chunk_bytes = int(settings.assemblyai_sample_rate * chunk_seconds * 2)
        self.min_chunk_bytes = int(settings.assemblyai_sample_rate * 0.05 * 2)
        self.env = _clean_env()
        self.proc: subprocess.Popen[bytes] | None = None
        self._temp_cookie_path: Path | None = None
        self._resolved_headers: dict[str, str] = {}

    def _cookie_file(self) -> str | None:
        if settings.ytdlp_cookies_file:
            return settings.ytdlp_cookies_file
        cookie_text = settings.ytdlp_cookies_b64 or settings.ytdlp_cookies
        if not cookie_text:
            return None

        if settings.ytdlp_cookies_b64:
            compact = "".join(cookie_text.split())
            cookie_bytes = base64.b64decode(compact)
        else:
            cookie_bytes = cookie_text.replace("\\n", "\n").encode("utf-8")

        cookie_path = Path(tempfile.gettempdir()) / "ytdlp_cookies.txt"
        cookie_path.write_bytes(cookie_bytes)
        self._temp_cookie_path = cookie_path
        return str(cookie_path)

    def _best_media_url(self, info: dict[str, Any]) -> str:
        if info.get("url"):
            return str(info["url"])
        requested_formats = info.get("requested_formats") or []
        if requested_formats:
            return str(requested_formats[0]["url"])
        formats = info.get("formats") or []
        if formats:
            audio_formats = [item for item in formats if item.get("acodec") != "none" and item.get("url")]
            candidates = audio_formats or [item for item in formats if item.get("url")]
            if candidates:
                return str(candidates[-1]["url"])
        raise RuntimeError("Could not resolve a playable media URL.")

    def _resolve_url(self) -> str:
        lowered = self.url.lower()
        if lowered.endswith((".m3u8", ".mp4", ".ts", ".webm", ".mp3", ".wav", ".m4a")):
            self._resolved_headers = {}
            return self.url

        ydl_opts: dict[str, object] = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "format": "bestaudio/best",
            "retries": 5,
            "fragment_retries": 5,
            "socket_timeout": 20,
            "http_headers": {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "en-US,en;q=0.9,ko;q=0.8",
            },
            "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
        }
        if settings.ytdlp_proxy_url:
            ydl_opts["proxy"] = settings.ytdlp_proxy_url
        else:
            ydl_opts["proxy"] = ""
        cookie_file = self._cookie_file()
        if cookie_file:
            ydl_opts["cookiefile"] = cookie_file

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(self.url, download=False)
            self._resolved_headers = {str(k): str(v) for k, v in (info.get("http_headers") or {}).items() if v}
            return self._best_media_url(info)

    def _ffmpeg_input_args(self, media_url: str) -> list[str]:
        args = [
            "-reconnect",
            "1",
            "-reconnect_streamed",
            "1",
            "-reconnect_delay_max",
            "5",
        ]
        user_agent = self._resolved_headers.get("User-Agent")
        if user_agent:
            args.extend(["-user_agent", user_agent])
        headers = [
            f"{name}: {value}"
            for name, value in self._resolved_headers.items()
            if name.lower() not in {"user-agent", "accept-encoding"}
        ]
        if headers:
            args.extend(["-headers", "\r\n".join(headers) + "\r\n"])
        args.extend(["-i", media_url])
        return args

    async def stream_pcm(self) -> AsyncGenerator[bytes, None]:
        media_url = await asyncio.to_thread(self._resolve_url)
        self.proc = subprocess.Popen(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                *self._ffmpeg_input_args(media_url),
                "-vn",
                "-f",
                "s16le",
                "-ar",
                str(settings.assemblyai_sample_rate),
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
        pending = bytearray()
        try:
            while True:
                assert self.proc.stdout is not None
                data = await asyncio.to_thread(self.proc.stdout.read, self.chunk_bytes)
                if not data:
                    break
                pending.extend(data)
                while len(pending) >= self.chunk_bytes:
                    chunk = bytes(pending[: self.chunk_bytes])
                    del pending[: self.chunk_bytes]
                    yield chunk
            if len(pending) >= self.min_chunk_bytes:
                yield bytes(pending)
        finally:
            await self.stop()

    async def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                await asyncio.to_thread(self.proc.wait, 3)
            except Exception:
                self.proc.kill()
        if self._temp_cookie_path:
            self._temp_cookie_path.unlink(missing_ok=True)
