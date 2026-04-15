from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import AsyncGenerator
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


@app.websocket("/ws/browser-audio")
async def browser_audio_ws(ws: WebSocket) -> None:
    await ws.accept()
    queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=200)
    events_task = asyncio.create_task(run_browser_audio_session(ws, queue))
    try:
        while True:
            message = await ws.receive()
            if message.get("bytes") is not None:
                await queue.put(message["bytes"])
            elif message.get("text") is not None:
                if message["text"] == "stop":
                    await queue.put(None)
                    break
    except WebSocketDisconnect:
        pass
    finally:
        await queue.put(None)
        await asyncio.gather(events_task, return_exceptions=True)


async def run_session(ws: WebSocket, url: str) -> None:
    source = URLAudioSource(url, settings.ingress_chunk_seconds)
    stt = AssemblyAIStreamingClient()

    async def audio() -> AsyncGenerator[bytes, None]:
        async for chunk in source.stream_pcm():
            yield chunk

    try:
        await run_stt_translation_session(
            ws,
            stt.stream(audio()),
            starting_message=(
                "YouTube 오디오를 준비하고 있습니다. "
                f"cookies={_yes_no(_youtube_cookies_configured())}, "
                f"proxy={_yes_no(bool(settings.ytdlp_proxy_url))}, "
                f"impersonate={settings.ytdlp_impersonate or 'off'}"
            ),
        )
    finally:
        await source.stop()


async def run_browser_audio_session(ws: WebSocket, queue: asyncio.Queue[bytes | None]) -> None:
    stt = AssemblyAIStreamingClient()

    async def audio() -> AsyncGenerator[bytes, None]:
        while True:
            chunk = await queue.get()
            if chunk is None:
                break
            if chunk:
                yield chunk

    await run_stt_translation_session(
        ws,
        stt.stream(audio()),
        starting_message="브라우저 탭 오디오를 AssemblyAI STT에 연결하고 있습니다.",
    )


async def run_stt_translation_session(ws: WebSocket, events, starting_message: str) -> None:
    assert translator is not None
    send_lock = asyncio.Lock()
    context: deque[str] = deque(maxlen=settings.translation_context_size)
    pending_finalize: dict[int, asyncio.Task] = {}
    pending_partial_translation: asyncio.Task | None = None
    segment_ids: dict[int, str] = {}
    finalized_turns: set[int] = set()
    segment_seq = 0

    async def send(payload: dict) -> None:
        async with send_lock:
            await ws.send_json(payload)

    async def finalize_turn(event: SttEvent, delay: float = 0.0) -> None:
        nonlocal segment_seq
        if delay:
            await asyncio.sleep(delay)
        text = event.text.strip()
        if not text or event.turn_order in finalized_turns:
            return
        finalized_turns.add(event.turn_order)

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
        try:
            korean, trans_ms = await translator.translate(text, list(context))
        except Exception as exc:
            await send(
                {
                    "type": "translation_error",
                    "segment_id": segment_id,
                    "seq": seq,
                    "message": _format_exception(exc),
                }
            )
            return

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

    async def translate_partial(text: str, turn_order: int) -> None:
        await asyncio.sleep(settings.partial_translation_delay)
        if turn_order in finalized_turns:
            return
        korean, trans_ms = await translator.translate(text, list(context))
        if turn_order in finalized_turns:
            return
        await send({"type": "partial_ko", "text": korean, "turn_order": turn_order, "trans_ms": trans_ms})

    def schedule_partial_translation(text: str, turn_order: int) -> None:
        nonlocal pending_partial_translation
        if pending_partial_translation and not pending_partial_translation.done():
            pending_partial_translation.cancel()
        if len(text.strip()) < 12:
            return
        pending_partial_translation = asyncio.create_task(translate_partial(text, turn_order))

    def schedule_finalize(event: SttEvent, delay: float) -> None:
        existing = pending_finalize.pop(event.turn_order, None)
        if existing:
            existing.cancel()
        pending_finalize[event.turn_order] = asyncio.create_task(finalize_turn(event, delay))

    await send({"type": "reset"})
    await send(
        {
            "type": "status",
            "level": "info",
            "message": starting_message,
        }
    )

    try:
        async for event in events:
            if event.type == "status":
                await send({"type": "status", "level": "info", "message": "AssemblyAI STT에 연결되었습니다."})
                continue
            if event.type != "turn" or not event.text:
                continue

            text = event.text.strip()
            if event.turn_order in finalized_turns:
                continue

            await send({"type": "partial", "text": text, "turn_order": event.turn_order})
            schedule_partial_translation(text, event.turn_order)

            if event.end_of_turn:
                schedule_finalize(event, 0.05)
            elif _looks_sentence_complete(text):
                schedule_finalize(event, settings.final_punctuation_delay)

        if pending_finalize:
            await asyncio.gather(*pending_finalize.values(), return_exceptions=True)
        await send({"type": "status", "level": "success", "message": "스트림이 종료되었습니다."})
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        detail = _format_exception(exc)
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
        await send({"type": "error", "message": message})
    finally:
        for task in pending_finalize.values():
            if not task.done():
                task.cancel()
        await asyncio.gather(*pending_finalize.values(), return_exceptions=True)
        if pending_partial_translation and not pending_partial_translation.done():
            pending_partial_translation.cancel()
            await asyncio.gather(pending_partial_translation, return_exceptions=True)


def _youtube_cookies_configured() -> bool:
    return bool(settings.ytdlp_cookies_b64 or settings.ytdlp_cookies or settings.ytdlp_cookies_file)


def _yes_no(value: bool) -> str:
    return "on" if value else "off"


def _format_exception(exc: BaseException) -> str:
    message = str(exc).strip()
    if message:
        return f"{type(exc).__name__}: {message}"
    return repr(exc)


def _looks_sentence_complete(text: str) -> bool:
    stripped = text.rstrip()
    if len(stripped) < 24:
        return False
    return stripped.endswith((".", "?", "!", ".”", "?”", "!”"))
