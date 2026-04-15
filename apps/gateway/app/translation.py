from __future__ import annotations

import time
from collections.abc import AsyncGenerator

from openai import AsyncOpenAI, BadRequestError

from .settings import settings


GLOSSARY = {
    "guidance": "가이던스",
    "gross margin": "매출총이익률",
    "operating margin": "영업이익률",
    "diluted EPS": "희석 EPS",
    "buyback": "자사주 매입",
    "capex": "설비투자",
    "free cash flow": "잉여현금흐름",
    "year over year": "전년 동기 대비",
    "quarter over quarter": "전분기 대비",
}


def _glossary_text() -> str:
    return "\n".join(f"- {source}: {target}" for source, target in GLOSSARY.items())


SYSTEM_PROMPT = f"""You are a real-time English-to-Korean interpreter.
Translate spoken English into natural Korean subtitles.
Return Korean only.
Preserve numbers, company names, and ticker symbols accurately.
If the source is incomplete, produce a natural Korean subtitle without adding facts.
Use this glossary when relevant:
{_glossary_text()}
"""


class Translator:
    def __init__(self) -> None:
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured.")
        self.client = AsyncOpenAI(api_key=settings.openai_api_key, timeout=settings.openai_timeout)

    async def translate(self, text: str, context: list[str]) -> tuple[str, int]:
        started = time.time()
        response = await self._create_response(settings.openai_model, text, context)
        return response.output_text.strip(), round((time.time() - started) * 1000)

    async def stream_translate(self, text: str, context: list[str]) -> AsyncGenerator[tuple[str, int | None], None]:
        started = time.time()
        collected: list[str] = []
        stream = await self.client.responses.create(
            model=self._model_name(),
            instructions=SYSTEM_PROMPT,
            input=self._user_input(text, context),
            stream=True,
        )
        async for event in stream:
            if event.type == "response.output_text.delta":
                collected.append(event.delta)
                yield event.delta, None
            elif event.type == "response.output_text.done":
                collected = [event.text]
        yield "".join(collected).strip(), round((time.time() - started) * 1000)

    def _user_input(self, text: str, context: list[str]) -> str:
        if not context:
            return f"Current sentence:\n{text}"
        context_text = "\n".join(f"- {item}" for item in context[-settings.translation_context_size :])
        return f"Previous English context:\n{context_text}\n\nCurrent sentence:\n{text}"

    async def _create_response(self, model: str, text: str, context: list[str]):
        try:
            return await self.client.responses.create(
                model=model,
                instructions=SYSTEM_PROMPT,
                input=self._user_input(text, context),
            )
        except BadRequestError as exc:
            if model != settings.openai_fallback_model and "model" in str(exc).lower():
                return await self.client.responses.create(
                    model=settings.openai_fallback_model,
                    instructions=SYSTEM_PROMPT,
                    input=self._user_input(text, context),
                )
            raise

    def _model_name(self) -> str:
        return settings.openai_model or settings.openai_fallback_model

    async def aclose(self) -> None:
        await self.client.close()
