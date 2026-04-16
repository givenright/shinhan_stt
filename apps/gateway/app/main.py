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


@app.get("/config")
async def config() -> dict[str, str | bool]:
    return {
        "default_stream_url": settings.default_stream_url,
        "auto_start_stream": settings.auto_start_stream,
        "openai_model": settings.openai_model,
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
                session_task = asyncio.create_task(run_url_session(ws, payload["url"]))
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


async def run_url_session(ws: WebSocket, url: str) -> None:
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


async def run_stt_translation_session(ws: WebSocket, events, starting_message: str) -> None:
    assert translator is not None
    send_lock = asyncio.Lock()
    context: deque[str] = deque(maxlen=settings.translation_context_size)
    pending_finalize: dict[int, asyncio.Task] = {}
    segment_ids: dict[int, str] = {}
    finalized_turns: set[int] = set()
    segment_seq = 0

    latest_partial: dict[str, object] = {"text": "", "turn_order": -1, "version": 0}
    partial_event = asyncio.Event()

    async def send(payload: dict) -> None:
        async with send_lock:
            await ws.send_json(payload)

    def segment_for(event: SttEvent) -> tuple[str, int]:
        nonlocal segment_seq
        segment_id = segment_ids.get(event.turn_order)
        if segment_id is None:
            segment_id = f"seg_{segment_seq:04d}"
            segment_ids[event.turn_order] = segment_id
            segment_seq += 1
        return segment_id, int(segment_id.split("_")[-1])

    async def partial_translation_worker() -> None:
        last_translated_text = ""
        while True:
            await partial_event.wait()
            partial_event.clear()
            if settings.partial_translation_delay > 0:
                await asyncio.sleep(settings.partial_translation_delay)

            text = str(latest_partial["text"]).strip()
            turn_order = int(latest_partial["turn_order"])
            version = int(latest_partial["version"])
            if not _should_partial_translate(text) or turn_order in finalized_turns or text == last_translated_text:
                continue

            started = time.time()
            try:
                korean, trans_ms = await translator.translate(text, list(context), partial=True)
            except Exception as exc:
                await send({"type": "partial_translation_error", "message": _format_exception(exc)})
                await asyncio.sleep(settings.partial_translation_interval)
                continue

            if korean and version <= int(latest_partial["version"]) and turn_order not in finalized_turns:
                last_translated_text = text
                await send(
                    {
                        "type": "partial_ko",
                        "text": korean,
                        "turn_order": turn_order,
                        "trans_ms": trans_ms,
                        "total_ms": round((time.time() - started) * 1000),
                    }
                )

            await asyncio.sleep(settings.partial_translation_interval)
            if str(latest_partial["text"]).strip() != last_translated_text:
                partial_event.set()

    async def finalize_turn(event: SttEvent, delay: float = 0.0) -> None:
        if delay:
            await asyncio.sleep(delay)
        text = event.text.strip()
        if not text or event.turn_order in finalized_turns:
            return
        finalized_turns.add(event.turn_order)

        segment_id, seq = segment_for(event)
        started = time.time()
        await send({"type": "segment", "segment_id": segment_id, "seq": seq, "phase": "final_en", "text": text})
        await send({"type": "translation_start", "segment_id": segment_id, "seq": seq})

        try:
            korean, trans_ms = await translator.translate(text, list(context), partial=False)
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

    def schedule_partial_translation(text: str, turn_order: int) -> None:
        if not _should_partial_translate(text):
            return
        latest_partial["text"] = text
        latest_partial["turn_order"] = turn_order
        latest_partial["version"] = int(latest_partial["version"]) + 1
        partial_event.set()

    def schedule_finalize(event: SttEvent, delay: float) -> None:
        existing = pending_finalize.pop(event.turn_order, None)
        if existing:
            existing.cancel()
        pending_finalize[event.turn_order] = asyncio.create_task(finalize_turn(event, delay))

    partial_worker = asyncio.create_task(partial_translation_worker())
    await send({"type": "reset"})
    await send({"type": "status", "level": "info", "message": starting_message})

    try:
        async for event in events:
            if event.type == "status":
                await send({"type": "status", "level": "info", "message": "AssemblyAI STT에 연결했습니다."})
                continue
            if event.type != "turn" or not event.text:
                continue

            text = event.text.strip()
            if event.turn_order in finalized_turns:
                continue

            await send({"type": "partial", "text": text, "turn_order": event.turn_order})
            schedule_partial_translation(text, event.turn_order)

            if event.end_of_turn:
                schedule_finalize(event, 0.0)
            elif _looks_sentence_complete(text):
                schedule_finalize(event, settings.final_punctuation_delay)

        if pending_finalize:
            await asyncio.gather(*pending_finalize.values(), return_exceptions=True)
        await send({"type": "status", "level": "success", "message": "스트림이 종료되었습니다."})
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        await send({"type": "error", "message": _user_facing_error(exc)})
    finally:
        partial_worker.cancel()
        for task in pending_finalize.values():
            if not task.done():
                task.cancel()
        await asyncio.gather(partial_worker, *pending_finalize.values(), return_exceptions=True)


def _youtube_cookies_configured() -> bool:
    return bool(settings.ytdlp_cookies_b64 or settings.ytdlp_cookies or settings.ytdlp_cookies_file)


def _yes_no(value: bool) -> str:
    return "on" if value else "off"


def _format_exception(exc: BaseException) -> str:
    message = str(exc).strip()
    if message:
        return f"{type(exc).__name__}: {message}"
    return repr(exc)


def _user_facing_error(exc: BaseException) -> str:
    detail = _format_exception(exc)
    if "Sign in to confirm" in detail or "not a bot" in detail:
        return (
            "YouTube가 Railway 서버를 봇으로 판정했습니다. "
            "YouTube를 메인으로 운영하려면 운영용 쿠키 또는 프록시가 필요합니다. "
            f"상세: {detail}"
        )
    return f"처리 중 오류가 발생했습니다. 상세: {detail}"


def _looks_sentence_complete(text: str) -> bool:
    stripped = text.rstrip()
    if len(stripped) < 12:
        return False
    return stripped.endswith((".", "?", "!"))


def _should_partial_translate(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) < 3:
        return False
    return any(char.isalpha() for char in stripped)
