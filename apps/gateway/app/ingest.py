from __future__ import annotations

import asyncio
import base64
import os
import subprocess
import tempfile
import time
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

    def _ydl_opts(self, use_impersonate: bool) -> dict[str, object]:
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
        if use_impersonate and settings.ytdlp_impersonate:
            ydl_opts["impersonate"] = settings.ytdlp_impersonate
        cookie_file = self._cookie_file()
        if cookie_file:
            ydl_opts["cookiefile"] = cookie_file
        return ydl_opts

    def _resolve_url(self) -> str:
        lowered = self.url.lower()
        if lowered.endswith((".m3u8", ".mp4", ".ts", ".webm", ".mp3", ".wav", ".m4a")):
            self._resolved_headers = {}
            return self.url

        attempts = [bool(settings.ytdlp_impersonate), False]
        last_error: BaseException | None = None
        for use_impersonate in dict.fromkeys(attempts):
            try:
                with yt_dlp.YoutubeDL(self._ydl_opts(use_impersonate)) as ydl:
                    info = ydl.extract_info(self.url, download=False)
                    self._resolved_headers = {
                        str(k): str(v) for k, v in (info.get("http_headers") or {}).items() if v
                    }
                    return self._best_media_url(info)
            except Exception as exc:
                last_error = exc
                if not use_impersonate:
                    break
        raise RuntimeError(f"yt-dlp URL resolve failed: {_format_exception(last_error)}")

    def _ffmpeg_input_args(self, media_url: str) -> list[str]:
        args = [
            "-reconnect",
            "1",
            "-reconnect_streamed",
            "1",
            "-reconnect_delay_max",
            "5",
            "-fflags",
            "nobuffer",
            "-flags",
            "low_delay",
            "-flush_packets",
            "1",
            "-probesize",
            "32768",
            "-analyzeduration",
            "0",
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
                "-af",
                "aresample=async=1:first_pts=0",
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
        stream_started = time.monotonic()
        emitted_seconds = 0.0
        emitted_any = False
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
                    await self._pace_audio(stream_started, emitted_seconds)
                    yield chunk
                    emitted_any = True
                    emitted_seconds += self._audio_seconds(chunk)
            if len(pending) >= self.min_chunk_bytes:
                chunk = bytes(pending)
                await self._pace_audio(stream_started, emitted_seconds)
                yield chunk
                emitted_any = True
            if not emitted_any:
                stderr = ""
                if self.proc.stderr is not None:
                    stderr = await asyncio.to_thread(self.proc.stderr.read)
                    stderr = stderr.decode("utf-8", errors="replace").strip()
                raise RuntimeError(f"ffmpeg produced no audio. {stderr}".strip())
        finally:
            await self.stop()

    async def _pace_audio(self, stream_started: float, emitted_seconds: float) -> None:
        target_time = stream_started + emitted_seconds
        delay = target_time - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)

    def _audio_seconds(self, chunk: bytes) -> float:
        bytes_per_sample = 2
        return len(chunk) / (settings.assemblyai_sample_rate * bytes_per_sample)

    async def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                await asyncio.to_thread(self.proc.wait, 3)
            except Exception:
                self.proc.kill()
        if self._temp_cookie_path:
            self._temp_cookie_path.unlink(missing_ok=True)


def _format_exception(exc: BaseException | None) -> str:
    if exc is None:
        return "unknown error"
    message = str(exc).strip()
    if message:
        return f"{type(exc).__name__}: {message}"
    return repr(exc)
