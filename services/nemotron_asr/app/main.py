from __future__ import annotations

import asyncio
import json
from collections import deque

import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from .config import settings
from .runtime import NemotronRuntime


app = FastAPI(title="nemotron-asr")
runtime: NemotronRuntime | None = None


@app.on_event("startup")
async def startup() -> None:
    global runtime
    runtime = NemotronRuntime()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "model_path": settings.model_path, "device": settings.device}


@app.websocket("/ws/transcribe")
async def ws_transcribe(ws: WebSocket) -> None:
    assert runtime is not None
    await ws.accept()
    current_frames: list[np.ndarray] = []
    rolling_frames: deque[np.ndarray] = deque()
    rolling_samples = 0
    partial_task = asyncio.create_task(partial_loop(ws, runtime, rolling_frames))
    try:
        while True:
            message = await ws.receive()
            if "bytes" in message and message["bytes"] is not None:
                audio = np.frombuffer(message["bytes"], dtype=np.float32)
                current_frames.append(audio)
                rolling_frames.append(audio)
                rolling_samples += len(audio)
                max_samples = int(settings.partial_window_seconds * settings.sample_rate)
                while rolling_samples > max_samples and rolling_frames:
                    rolling_samples -= len(rolling_frames.popleft())
            elif "text" in message and message["text"] is not None:
                payload = json.loads(message["text"])
                if payload["type"] == "segment_end":
                    if current_frames:
                        audio = np.concatenate(current_frames)
                        text, stt_ms = await runtime.transcribe(audio)
                        current_frames = []
                        await ws.send_text(json.dumps({"type": "final", "text": text, "stt_ms": stt_ms, "total_ms": stt_ms}))
                elif payload["type"] == "stream_end":
                    if current_frames:
                        audio = np.concatenate(current_frames)
                        text, stt_ms = await runtime.transcribe(audio)
                        await ws.send_text(json.dumps({"type": "final", "text": text, "stt_ms": stt_ms, "total_ms": stt_ms}))
                    await ws.send_text(json.dumps({"type": "status", "level": "success", "message": "Stream ended."}))
                    break
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        await ws.send_text(json.dumps({"type": "status", "level": "error", "message": f"STT runtime error: {exc}"}))
    finally:
        partial_task.cancel()
        await asyncio.gather(partial_task, return_exceptions=True)


async def partial_loop(ws: WebSocket, runtime: NemotronRuntime, rolling_frames: deque[np.ndarray]) -> None:
    last_text = ""
    while True:
        await asyncio.sleep(settings.partial_update_interval)
        if not rolling_frames:
            continue
        audio = np.concatenate(list(rolling_frames))
        text, stt_ms = await runtime.transcribe(audio)
        if text and text != last_text:
            last_text = text
            await ws.send_text(json.dumps({"type": "partial", "text": text, "stt_ms": stt_ms}))
