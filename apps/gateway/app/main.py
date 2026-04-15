from __future__ import annotations

import asyncio
import time
from collections import deque
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .assemblyai_stt import AssemblyAIStreamingClient, SttEvent
from .ingest import URLAudioSource
from .settings import settings
from .translation import Translator


app = FastAPI(title="shinhan-live-stt")
translator: Translator | None = None
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.on_event("startup")
async def on_startup() -> None:
    global translator
    translator = Translator()


@app.on_event("shutdown")
async def on_shutdown() -> None:
    if translator:
        await translator.aclose()


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "stt": "assemblyai",
        "translation": "openai",
        "model": settings.openai_model,
        "youtube_cookies": "configured" if _youtube_cookies_configured() else "missing",
        "youtube_proxy": "configured" if settings.ytdlp_proxy_url else "missing",
        "youtube_impersonate": settings.ytdlp_impersonate or "disabled",
    }


@app.websocket("/ws/ui")
async def ui_ws(ws: WebSocket) -> None:
    await ws.accept()
    session_task: asyncio.Task | None = None
    try:
        while True:
            payload = await ws.receive_json()
            if payload.get("type") == "start":
                if session_task and not session_task.done():
                    session_task.cancel()
                    await asyncio.gather(session_task, return_exceptions=True)
                session_task = asyncio.create_task(run_session(ws, payload["url"]))
            elif payload.get("type") == "stop":
                if session_task and not session_task.done():
                    session_task.cancel()
                    await asyncio.gather(session_task, return_exceptions=True)
                await ws.send_json({"type": "status", "level": "info", "message": "세션을 중지했습니다."})
    except WebSocketDisconnect:
        pass
    finally:
        if session_task and not session_task.done():
            session_task.cancel()
            await asyncio.gather(session_task, return_exceptions=True)


async def run_session(ws: WebSocket, url: str) -> None:
    assert translator is not None
    send_lock = asyncio.Lock()
    context: deque[str] = deque(maxlen=settings.translation_context_size)
    pending_finalize: dict[int, asyncio.Task] = {}
    segment_ids: dict[int, str] = {}
    segment_seq = 0
    source = URLAudioSource(url, settings.ingress_chunk_seconds)
    stt = AssemblyAIStreamingClient()

    async def send(payload: dict) -> None:
        async with send_lock:
            await ws.send_json(payload)

    async def finalize_turn(event: SttEvent, delay: float = 0.0) -> None:
        nonlocal segment_seq
        if delay:
            await asyncio.sleep(delay)
        text = event.text.strip()
        if not text:
            return

        segment_id = segment_ids.get(event.turn_order)
        if segment_id is None:
            segment_id = f"seg_{segment_seq:04d}"
            segment_ids[event.turn_order] = segment_id
            segment_seq += 1
        seq = int(segment_id.split("_")[-1])
        started = time.time()
        await send(
            {
                "type": "segment",
                "segment_id": segment_id,
                "seq": seq,
                "phase": "final_en",
                "text": text,
                "formatted": event.turn_is_formatted,
            }
        )

        await send({"type": "translation_start", "segment_id": segment_id, "seq": seq})
        translated_parts: list[str] = []
        trans_ms = 0
        async for piece, elapsed_ms in translator.stream_translate(text, list(context)):
            if elapsed_ms is None:
                translated_parts.append(piece)
                await send(
                    {
                        "type": "translation_delta",
                        "segment_id": segment_id,
                        "seq": seq,
                        "text": piece,
                    }
                )
            else:
                trans_ms = elapsed_ms

        korean = "".join(translated_parts).strip()
        if korean:
            context.append(text)
            await send(
                {
                    "type": "segment",
                    "segment_id": segment_id,
                    "seq": seq,
                    "phase": "final_ko",
                    "text": korean,
                    "trans_ms": trans_ms,
                    "total_ms": round((time.time() - started) * 1000),
                }
            )

    await send({"type": "reset"})
    await send(
        {
            "type": "status",
            "level": "info",
            "message": (
                "YouTube 오디오를 준비하고 있습니다. "
                f"cookies={_yes_no(_youtube_cookies_configured())}, "
                f"proxy={_yes_no(bool(settings.ytdlp_proxy_url))}, "
                f"impersonate={settings.ytdlp_impersonate or 'off'}"
            ),
        }
    )

    try:
        async for event in stt.stream(source.stream_pcm()):
            if event.type == "status":
                await send({"type": "status", "level": "info", "message": "AssemblyAI STT에 연결되었습니다."})
                continue
            if event.type != "turn" or not event.text:
                continue

            if not event.end_of_turn:
                await send({"type": "partial", "text": event.text, "turn_order": event.turn_order})
                continue

            existing = pending_finalize.pop(event.turn_order, None)
            if existing:
                existing.cancel()
                await asyncio.gather(existing, return_exceptions=True)

            delay = 0.0 if event.turn_is_formatted else 0.7
            task = asyncio.create_task(finalize_turn(event, delay))
            pending_finalize[event.turn_order] = task

        if pending_finalize:
            await asyncio.gather(*pending_finalize.values(), return_exceptions=True)
        await send({"type": "status", "level": "success", "message": "스트림이 종료되었습니다."})
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        detail = str(exc)
        if "Sign in to confirm" in detail or "not a bot" in detail:
            message = (
                "YouTube가 Railway 서버를 봇으로 판정했습니다. "
                "Railway Variables에 YTDLP_COOKIES_B64를 넣고 재배포해야 합니다. "
                "이미 넣었다면 값이 비었거나 만료된 쿠키일 수 있습니다. "
                f"상세: {detail}"
            )
        else:
            message = (
                "처리 중 오류가 발생했습니다. YouTube 차단이면 Railway 환경변수에 "
                f"쿠키/프록시를 설정해야 할 수 있습니다. 상세: {detail}"
            )
        await send(
            {
                "type": "error",
                "message": message,
            }
        )
    finally:
        for task in pending_finalize.values():
            if not task.done():
                task.cancel()
        await asyncio.gather(*pending_finalize.values(), return_exceptions=True)
        await source.stop()


def _youtube_cookies_configured() -> bool:
    return bool(settings.ytdlp_cookies_b64 or settings.ytdlp_cookies or settings.ytdlp_cookies_file)


def _yes_no(value: bool) -> str:
    return "on" if value else "off"
