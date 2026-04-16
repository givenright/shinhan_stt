from __future__ import annotations

import asyncio
import base64
import os
import re
import subprocess
import tempfile
import time
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import httpx
import yt_dlp
from curl_cffi import requests as curl_requests

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

    def _is_direct_media_url(self, url: str) -> bool:
        lowered = url.lower().split("?", 1)[0]
        return lowered.endswith((".m3u8", ".mp4", ".ts", ".webm", ".mp3", ".wav", ".m4a", ".aac"))

    def _html_headers(self) -> dict[str, str]:
        return {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,ko;q=0.8",
        }

    def _fetch_text(self, url: str) -> tuple[str, str]:
        try:
            with httpx.Client(
                headers=self._html_headers(),
                follow_redirects=True,
                timeout=20.0,
                trust_env=False,
                proxy=settings.ytdlp_proxy_url or None,
            ) as client:
                response = client.get(url)
                response.raise_for_status()
                return response.text, str(response.url)
        except Exception:
            request_kwargs: dict[str, object] = {
                "headers": self._html_headers(),
                "impersonate": settings.ytdlp_impersonate or "chrome",
                "timeout": 20,
                "allow_redirects": True,
            }
            if settings.ytdlp_proxy_url:
                request_kwargs["proxy"] = settings.ytdlp_proxy_url
            proxy_keys = ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"]
            saved_proxy_env = {key: os.environ.get(key) for key in proxy_keys}
            if not settings.ytdlp_proxy_url:
                for key in proxy_keys:
                    os.environ.pop(key, None)
            try:
                response = curl_requests.get(url, **request_kwargs)
            finally:
                for key, value in saved_proxy_env.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value
            response.raise_for_status()
            return response.text, response.url

    def _webpage_candidates(self, page_url: str) -> list[str]:
        try:
            html, final_url = self._fetch_text(page_url)
        except Exception:
            return []

        candidates: list[str] = []
        raw_matches: list[str] = []
        raw_matches.extend(re.findall(r"""(?:src|href|data-url|data-src)=["']([^"']+)["']""", html, flags=re.I))
        raw_matches.extend(re.findall(r"""https?:\\/\\/[^"'<>\s]+""", html))
        raw_matches.extend(re.findall(r"""https?://[^"'<>\s]+""", html))
        script_sources = [
            urljoin(final_url, item.replace("&amp;", "&").strip())
            for item in re.findall(r"""<script[^>]+src=["']([^"']+)["']""", html, flags=re.I)
        ]

        for script_url in script_sources[:20]:
            try:
                script_text, _ = self._fetch_text(script_url)
            except Exception:
                continue
            raw_matches.extend(re.findall(r"""https?:\\/\\/[^"'<>\s]+""", script_text))
            raw_matches.extend(re.findall(r"""https?://[^"'<>\s]+""", script_text))
            raw_matches.extend(re.findall(r"""(?:src|href|url|webcast|iframe|embed)["']?\s*[:=]\s*["']([^"']+)["']""", script_text, flags=re.I))

        for raw in raw_matches:
            cleaned = raw.replace("\\/", "/").replace("&amp;", "&").strip()
            if not cleaned or cleaned.startswith(("data:", "mailto:", "tel:")):
                continue
            absolute = urljoin(final_url, cleaned)
            normalized = self._normalize_candidate_url(absolute)
            if normalized and normalized not in candidates:
                candidates.append(normalized)

        ranked = sorted(candidates, key=self._candidate_rank)
        return ranked[:40]

    def _normalize_candidate_url(self, url: str) -> str | None:
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        path = parsed.path
        if "youtube.com" in host and path.startswith("/embed/"):
            video_id = path.split("/embed/", 1)[1].split("/", 1)[0]
            if video_id:
                return f"https://www.youtube.com/watch?v={video_id}"
        if "youtube.com" in host and path.startswith("/live/"):
            video_id = path.split("/live/", 1)[1].split("/", 1)[0]
            if video_id:
                return f"https://www.youtube.com/watch?v={video_id}"
        if "youtube.com" in host and path == "/watch":
            video_id = parse_qs(parsed.query).get("v", [""])[0]
            if video_id:
                return f"https://www.youtube.com/watch?v={video_id}"
        if "youtu.be" in host:
            video_id = path.strip("/").split("/", 1)[0]
            if video_id:
                return f"https://www.youtube.com/watch?v={video_id}"
        if self._is_direct_media_url(url):
            return url
        if any(domain in host for domain in ("youtube.com", "youtu.be", "vimeo.com", "brightcove", "livestream", "on24", "q4cdn", "akamaized", "cloudfront")):
            return url
        return None

    def _candidate_rank(self, url: str) -> tuple[int, str]:
        lowered = url.lower()
        if ".m3u8" in lowered:
            return (0, url)
        if "youtube.com/watch" in lowered or "youtu.be" in lowered:
            return (1, url)
        if any(item in lowered for item in ("brightcove", "livestream", "on24")):
            return (2, url)
        if self._is_direct_media_url(url):
            return (3, url)
        return (9, url)

    def _youtube_client_groups(self) -> list[list[str]]:
        configured = [item.strip() for item in settings.ytdlp_player_clients.split(",") if item.strip()]
        fallback = ["mweb", "web_safari", "web_embedded", "web_creator", "ios", "android", "tv"]
        ordered = []
        for item in [*configured, *fallback]:
            if item and item not in ordered:
                ordered.append(item)
        groups = [[item] for item in ordered]
        if ordered:
            groups.append(ordered)
        return groups

    def _ydl_opts(self, use_impersonate: bool, player_clients: list[str]) -> dict[str, object]:
        youtube_args: dict[str, list[str]] = {
            "player_client": player_clients,
        }
        if settings.ytdlp_visitor_data:
            youtube_args["player_skip"] = ["webpage", "configs"]
            youtube_args["visitor_data"] = [settings.ytdlp_visitor_data]
        if settings.ytdlp_po_token:
            youtube_args["po_token"] = [settings.ytdlp_po_token]
        if settings.ytdlp_data_sync_id:
            youtube_args["data_sync_id"] = [settings.ytdlp_data_sync_id]

        ydl_opts: dict[str, object] = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "skip_download": True,
            "cachedir": False,
            "format": "bestaudio[ext=m4a]/bestaudio/best",
            "retries": 10,
            "extractor_retries": 5,
            "fragment_retries": 10,
            "file_access_retries": 5,
            "socket_timeout": 20,
            "sleep_interval_requests": settings.ytdlp_sleep_requests,
            "geo_bypass": True,
            "force_ipv4": settings.ytdlp_force_ipv4,
            "http_headers": {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "en-US,en;q=0.9,ko;q=0.8",
                "Origin": "https://www.youtube.com",
                "Referer": "https://www.youtube.com/",
            },
            "extractor_args": {"youtube": youtube_args},
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
        if self._is_direct_media_url(self.url):
            self._resolved_headers = {}
            return self.url

        page_candidates = self._webpage_candidates(self.url)
        candidates = []
        candidates.extend(candidate for candidate in page_candidates if candidate != self.url)
        candidates.append(self.url)
        tried: list[str] = []
        impersonation_attempts = [bool(settings.ytdlp_impersonate), False]
        client_groups = self._youtube_client_groups()
        last_error: BaseException | None = None
        for candidate in candidates:
            if self._is_direct_media_url(candidate):
                tried.append(candidate)
                self._resolved_headers = {}
                return candidate
            for player_clients in client_groups:
                for use_impersonate in dict.fromkeys(impersonation_attempts):
                    tried.append(f"{candidate} clients={','.join(player_clients)} impersonate={use_impersonate}")
                    try:
                        with yt_dlp.YoutubeDL(self._ydl_opts(use_impersonate, player_clients)) as ydl:
                            info = ydl.extract_info(candidate, download=False)
                            self._resolved_headers = {
                                str(k): str(v) for k, v in (info.get("http_headers") or {}).items() if v
                            }
                            return self._best_media_url(info)
                    except Exception as exc:
                        last_error = exc
        tried_text = ", ".join(tried[:6])
        raise RuntimeError(f"yt-dlp URL resolve failed after trying [{tried_text}]: {_format_exception(last_error)}")

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
