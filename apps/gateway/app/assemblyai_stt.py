from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterable, AsyncGenerator
from dataclasses import dataclass
from urllib.parse import urlencode

import websockets

from .settings import settings


@dataclass(frozen=True)
class SttEvent:
    type: str
    text: str = ""
    turn_order: int = 0
    end_of_turn: bool = False
    turn_is_formatted: bool = False
    raw: dict | None = None


class AssemblyAIStreamingClient:
    def __init__(self) -> None:
        if not settings.assemblyai_api_key:
            raise RuntimeError("ASSEMBLYAI_API_KEY is not configured.")

    def _endpoint(self) -> str:
        params: dict[str, str | int | bool] = {
            "sample_rate": settings.assemblyai_sample_rate,
            "encoding": "pcm_s16le",
            "min_turn_silence": settings.assemblyai_min_turn_silence,
            "max_turn_silence": settings.assemblyai_max_turn_silence,
        }
        if settings.assemblyai_speech_model:
            params["speech_model"] = settings.assemblyai_speech_model
        if settings.assemblyai_speech_model != "u3-rt-pro":
            params["format_turns"] = str(settings.assemblyai_format_turns).lower()
        return f"{settings.assemblyai_streaming_url}?{urlencode(params)}"

    async def stream(self, audio_chunks: AsyncIterable[bytes]) -> AsyncGenerator[SttEvent, None]:
        queue: asyncio.Queue[SttEvent | BaseException | None] = asyncio.Queue()
        headers = {"Authorization": settings.assemblyai_api_key}

        async with websockets.connect(
            self._endpoint(),
            additional_headers=headers,
            max_size=16 * 1024 * 1024,
            ping_interval=20,
            ping_timeout=20,
        ) as ws:
            reader = asyncio.create_task(self._read_events(ws, queue))
            sender = asyncio.create_task(self._send_audio(ws, audio_chunks, queue))
            try:
                while True:
                    item = await queue.get()
                    if item is None:
                        break
                    if isinstance(item, BaseException):
                        raise item
                    yield item
            finally:
                sender.cancel()
                reader.cancel()
                await asyncio.gather(sender, reader, return_exceptions=True)

    async def _send_audio(
        self,
        ws,
        audio_chunks: AsyncIterable[bytes],
        queue: asyncio.Queue[SttEvent | BaseException | None],
    ) -> None:
        try:
            async for chunk in audio_chunks:
                if chunk:
                    await ws.send(chunk)
            await ws.send(json.dumps({"type": "Terminate"}))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await queue.put(exc)

    async def _read_events(self, ws, queue: asyncio.Queue[SttEvent | BaseException | None]) -> None:
        try:
            async for raw in ws:
                payload = json.loads(raw)
                event_type = payload.get("type", "")
                if event_type == "Begin":
                    await queue.put(SttEvent(type="status", raw=payload))
                elif event_type == "Turn":
                    await queue.put(
                        SttEvent(
                            type="turn",
                            text=(payload.get("transcript") or "").strip(),
                            turn_order=int(payload.get("turn_order") or 0),
                            end_of_turn=bool(payload.get("end_of_turn")),
                            turn_is_formatted=bool(payload.get("turn_is_formatted")),
                            raw=payload,
                        )
                    )
                elif event_type == "Termination":
                    await queue.put(SttEvent(type="status", raw=payload))
                    await queue.put(None)
                    break
                else:
                    await queue.put(SttEvent(type="status", raw=payload))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await queue.put(exc)
