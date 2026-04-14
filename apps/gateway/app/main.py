from __future__ import annotations

import asyncio
import json
from collections import deque
from pathlib import Path

import websockets
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .ingest import URLAudioSource, VADBuffer
from .settings import settings
from .translation import Translator


app = FastAPI(title="nemotron-gateway")
translator = Translator()
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.on_event("shutdown")
async def on_shutdown() -> None:
    await translator.aclose()


@app.websocket("/ws/ui")
async def ui_ws(ws: WebSocket) -> None:
    await ws.accept()
    ingest_task: asyncio.Task | None = None
    try:
        while True:
            payload = await ws.receive_json()
            if payload.get("type") == "start":
                if ingest_task and not ingest_task.done():
                    ingest_task.cancel()
                    await asyncio.gather(ingest_task, return_exceptions=True)
                ingest_task = asyncio.create_task(run_session(ws, payload["url"]))
            elif payload.get("type") == "stop":
                if ingest_task and not ingest_task.done():
                    ingest_task.cancel()
                    await asyncio.gather(ingest_task, return_exceptions=True)
                await ws.send_json({"type": "status", "level": "info", "message": "Session stopped."})
    except WebSocketDisconnect:
        pass
    finally:
        if ingest_task and not ingest_task.done():
            ingest_task.cancel()
            await asyncio.gather(ingest_task, return_exceptions=True)


async def run_session(ws: WebSocket, url: str) -> None:
    await ws.send_json({"type": "reset"})
    await ws.send_json({"type": "status", "level": "info", "message": f"URL stream started: {url}"})
    context: deque[str] = deque(maxlen=2)
    source = URLAudioSource(url, settings.ingress_chunk_seconds)
    vad = VADBuffer(
        settings.vad_silence_threshold,
        settings.vad_silence_duration,
        settings.vad_min_speech_seconds,
        settings.vad_max_buffer_seconds,
    )
    async with websockets.connect(settings.stt_ws_url, max_size=16 * 1024 * 1024) as stt_ws:
        stt_reader = asyncio.create_task(read_stt_events(ws, stt_ws, context))
        try:
            async for chunk in source.stream():
                await stt_ws.send(chunk.astype("float32").tobytes())
                utterance = vad.add(chunk)
                if utterance is not None:
                    await stt_ws.send(json.dumps({"type": "segment_end"}))
            if vad.flush() is not None:
                await stt_ws.send(json.dumps({"type": "segment_end"}))
            await stt_ws.send(json.dumps({"type": "stream_end"}))
            await stt_reader
        finally:
            stt_reader.cancel()
            await asyncio.gather(stt_reader, return_exceptions=True)
            await source.stop()


async def read_stt_events(ws: WebSocket, stt_ws, context: deque[str]) -> None:
    segment_seq = 0
    async for raw in stt_ws:
        payload = json.loads(raw)
        if payload["type"] == "partial":
            await ws.send_json(payload)
        elif payload["type"] == "final":
            segment_id = f"seg_{segment_seq:04d}"
            segment_seq += 1
            await ws.send_json(
                {
                    "type": "segment",
                    "segment_id": segment_id,
                    "seq": segment_seq - 1,
                    "phase": "final_en",
                    "text": payload["text"],
                    "stt_ms": payload.get("stt_ms", 0),
                    "total_ms": payload.get("stt_ms", 0),
                }
            )
            korean = await translator.translate(payload["text"], list(context))
            context.append(payload["text"])
            await ws.send_json(
                {
                    "type": "segment",
                    "segment_id": segment_id,
                    "seq": segment_seq - 1,
                    "phase": "final_ko",
                    "text": korean,
                    "stt_ms": payload.get("stt_ms", 0),
                    "trans_ms": payload.get("trans_ms", 0),
                    "total_ms": payload.get("total_ms", payload.get("stt_ms", 0)),
                }
            )
        elif payload["type"] == "status":
            await ws.send_json(payload)
