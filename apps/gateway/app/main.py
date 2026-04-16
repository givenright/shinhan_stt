from __future__ import annotations

import asyncio
import json
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


class CaptionHub:
    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()
        self.history: deque[dict] = deque(maxlen=60)
        self.lock = asyncio.Lock()

    async def subscribe(self, ws: WebSocket) -> None:
        async with self.lock:
            self.clients.add(ws)
            history = list(self.history)
        await ws.send_json({"type": "reset"})
        await ws.send_json({"type": "status", "level": "info", "message": "실시간 자막 방송을 수신 중입니다."})
        for payload in history:
            await ws.send_json(payload)

    async def unsubscribe(self, ws: WebSocket) -> None:
        async with self.lock:
            self.clients.discard(ws)

    async def broadcast(self, payload: dict) -> None:
        if payload.get("type") not in {
            "reset",
            "status",
            "error",
            "partial",
            "partial_ko",
            "partial_translation_error",
            "translation_start",
            "translation_error",
            "segment",
        }:
            return
        if payload.get("type") == "reset":
            self.history.clear()
        elif payload.get("type") != "status":
            self.history.append(payload)

        async with self.lock:
            clients = list(self.clients)

        dead: list[WebSocket] = []
        for client in clients:
            try:
                await client.send_json(payload)
            except Exception:
                dead.append(client)

        if dead:
            async with self.lock:
                for client in dead:
                    self.clients.discard(client)


app = FastAPI(title="shinhan-live-stt")
translator: Translator | None = None
caption_hub = CaptionHub()
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
async def health() -> dict[str, str | int]:
    return {
        "status": "ok",
        "stt": "assemblyai",
        "translation": "openai",
        "model": settings.openai_model,
        "viewer_clients": len(caption_hub.clients),
        "youtube_cookies": "configured" if _youtube_cookies_configured() else "missing",
        "youtube_proxy": "configured" if settings.ytdlp_proxy_url else "missing",
        "youtube_impersonate": settings.ytdlp_impersonate or "disabled",
        "youtube_player_clients": settings.ytdlp_player_clients,
        "youtube_visitor_data": "configured" if settings.ytdlp_visitor_data else "missing",
        "youtube_po_token": "configured" if settings.ytdlp_po_token else "missing",
    }


@app.get("/config")
async def config() -> dict[str, str | bool]:
    return {
        "default_stream_url": settings.default_stream_url,
        "auto_start_stream": settings.auto_start_stream,
        "openai_model": settings.openai_model,
    }


@app.websocket("/ws/viewer")
async def viewer_ws(ws: WebSocket) -> None:
    await ws.accept()
    await caption_hub.subscribe(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await caption_hub.unsubscribe(ws)


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
                session_task = asyncio.create_task(run_url_session(ws, payload["url"], broadcast=True))
            elif payload.get("type") == "stop":
                if session_task and not session_task.done():
                    session_task.cancel()
                    await asyncio.gather(session_task, return_exceptions=True)
                await ws.send_json({"type": "status", "level": "info", "message": "세션을 중지했습니다."})
                await caption_hub.broadcast({"type": "status", "level": "info", "message": "관리자 URL 송출을 중지했습니다."})
    except WebSocketDisconnect:
        pass
    finally:
        if session_task and not session_task.done():
            session_task.cancel()
            await asyncio.gather(session_task, return_exceptions=True)


@app.websocket("/ws/browser-audio")
async def browser_audio_ws(ws: WebSocket) -> None:
    await ws.accept()
    queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=100)
    stt = AssemblyAIStreamingClient()

    async def audio() -> AsyncGenerator[bytes, None]:
        while True:
            chunk = await queue.get()
            if chunk is None:
                break
            yield chunk

    session_task = asyncio.create_task(
        run_stt_translation_session(
            ws,
            stt.stream(audio()),
            starting_message="관리자 오디오 송출이 STT에 연결되었습니다.",
            broadcast=True,
        )
    )
    try:
        while True:
            message = await ws.receive()
            if message.get("bytes") is not None:
                chunk = message["bytes"]
                if queue.full():
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                await queue.put(chunk)
            elif message.get("text"):
                try:
                    payload = json.loads(message["text"])
                except json.JSONDecodeError:
                    continue
                if payload.get("type") == "stop":
                    break
            elif message.get("type") == "websocket.disconnect":
                break
    except WebSocketDisconnect:
        pass
    finally:
        await queue.put(None)
        if not session_task.done():
            session_task.cancel()
        await asyncio.gather(session_task, return_exceptions=True)
        await caption_hub.broadcast({"type": "status", "level": "info", "message": "관리자 오디오 송출이 종료되었습니다."})


async def run_url_session(ws: WebSocket, url: str, *, broadcast: bool = False) -> None:
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
                f"impersonate={settings.ytdlp_impersonate or 'off'}, "
                f"clients={settings.ytdlp_player_clients}"
            ),
            broadcast=broadcast,
        )
    finally:
        await source.stop()


async def run_stt_translation_session(ws: WebSocket, events, starting_message: str, *, broadcast: bool = False) -> None:
    assert translator is not None
    send_lock = asyncio.Lock()
    context: deque[str] = deque(maxlen=settings.translation_context_size)
    pending_finalize: dict[int, asyncio.Task] = {}
    segment_ids: dict[int, str] = {}
    finalized_turns: set[int] = set()
    segment_seq = 0

    latest_partial: dict[str, object] = {"text": "", "turn_order": -1, "version": 0}
    partial_event = asyncio.Event()
    partial_tasks: set[asyncio.Task] = set()

    async def send(payload: dict) -> None:
        async with send_lock:
            await ws.send_json(payload)
        if broadcast:
            await caption_hub.broadcast(payload)

    def segment_for(event: SttEvent) -> tuple[str, int]:
        nonlocal segment_seq
        segment_id = segment_ids.get(event.turn_order)
        if segment_id is None:
            segment_id = f"seg_{segment_seq:04d}"
            segment_ids[event.turn_order] = segment_id
            segment_seq += 1
        return segment_id, int(segment_id.split("_")[-1])

    async def translate_partial_snapshot(text: str, turn_order: int, version: int) -> None:
        started = time.time()
        await send({"type": "translation_start", "segment_id": f"partial_{turn_order}", "seq": turn_order})
        try:
            korean, trans_ms = await translator.translate(text, list(context), partial=True)
        except Exception as exc:
            await send({"type": "partial_translation_error", "message": _format_exception(exc)})
            return

        if not korean:
            return
        await send(
            {
                "type": "partial_ko",
                "text": korean,
                "source_text": text,
                "turn_order": turn_order,
                "version": version,
                "trans_ms": trans_ms,
                "total_ms": round((time.time() - started) * 1000),
            }
        )

    async def partial_translation_worker() -> None:
        last_started_text = ""
        last_started_at = 0.0
        while True:
            try:
                await asyncio.wait_for(partial_event.wait(), timeout=0.1)
                partial_event.clear()
            except TimeoutError:
                pass

            text = str(latest_partial["text"]).strip()
            turn_order = int(latest_partial["turn_order"])
            version = int(latest_partial["version"])
            now = time.monotonic()

            if not _should_partial_translate(text):
                continue
            if text == last_started_text:
                continue
            if now - last_started_at < settings.partial_translation_interval and partial_tasks:
                continue
            if len(partial_tasks) >= settings.partial_translation_concurrency:
                continue

            last_started_text = text
            last_started_at = now
            task = asyncio.create_task(translate_partial_snapshot(text, turn_order, version))
            partial_tasks.add(task)
            task.add_done_callback(partial_tasks.discard)

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
            await send({"type": "partial", "text": text, "turn_order": event.turn_order})
            schedule_partial_translation(text, event.turn_order)

            if event.end_of_turn:
                schedule_finalize(event, 0.0)

        if pending_finalize:
            await asyncio.gather(*pending_finalize.values(), return_exceptions=True)
        await send({"type": "status", "level": "success", "message": "스트림이 종료되었습니다."})
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        await send({"type": "error", "message": _user_facing_error(exc)})
    finally:
        partial_worker.cancel()
        for task in partial_tasks:
            if not task.done():
                task.cancel()
        for task in pending_finalize.values():
            if not task.done():
                task.cancel()
        await asyncio.gather(partial_worker, *partial_tasks, *pending_finalize.values(), return_exceptions=True)


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
            "관리자 오디오 송출 모드를 사용하거나, 운영용 쿠키/프록시를 설정하세요. "
            f"상세: {detail}"
        )
    return f"처리 중 오류가 발생했습니다. 상세: {detail}"


def _should_partial_translate(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) < 2:
        return False
    return any(char.isalpha() for char in stripped)
