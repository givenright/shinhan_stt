from __future__ import annotations

import re
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

BAD_TRANSLATION_PATTERNS = (
    "번역할 내용이 부족",
    "문맥이나 추가 정보",
    "추가 정보를 제공",
    "죄송하지만",
    "내용이 충분하지",
    "cannot translate",
    "not enough context",
)

FILLERS = {"um", "uh", "umm", "uhh", "ah", "oh", "er", "hmm"}


def _glossary_text() -> str:
    return "\n".join(f"- {source}: {target}" for source, target in GLOSSARY.items())


SYSTEM_PROMPT = f"""You are a fast live English-to-Korean subtitle translator.
Translate English speech into natural Korean subtitles.

Rules:
- Output Korean translation only.
- Never apologize.
- Never say there is not enough context.
- Never ask for more information.
- Translate partial fragments immediately. The fragment may be revised later.
- If the input is only filler such as "um" or repeated noise, output an empty string.
- Preserve numbers, company names, product names, and ticker symbols accurately.
- Keep subtitles concise and natural for on-screen display.

Glossary:
{_glossary_text()}
"""


class Translator:
    def __init__(self) -> None:
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured.")
        self.client = AsyncOpenAI(api_key=settings.openai_api_key, timeout=settings.openai_timeout)

    async def translate(self, text: str, context: list[str], *, partial: bool = False) -> tuple[str, int]:
        started = time.time()
        cleaned = _clean_source(text)
        if not _should_translate(cleaned):
            return "", 0

        response = await self._create_response(settings.openai_model, cleaned, context, partial=partial)
        translated = _clean_translation(response.output_text)
        if _is_bad_translation(translated):
            translated = _fallback_fragment_translation(cleaned)
        return translated, round((time.time() - started) * 1000)

    async def stream_translate(self, text: str, context: list[str]) -> AsyncGenerator[tuple[str, int | None], None]:
        korean, elapsed = await self.translate(text, context)
        if korean:
            yield korean, None
        yield korean, elapsed

    def _user_input(self, text: str, context: list[str], *, partial: bool) -> str:
        context_text = "\n".join(f"- {item}" for item in context[-settings.translation_context_size :])
        mode = "Live partial caption. Translate now, even if incomplete." if partial else "Completed caption. Produce the best final Korean subtitle."
        if context_text:
            return f"{mode}\n\nPrevious English context:\n{context_text}\n\nEnglish to translate:\n{text}"
        return f"{mode}\n\nEnglish to translate:\n{text}"

    async def _create_response(self, model: str, text: str, context: list[str], *, partial: bool):
        max_output_tokens = 90 if partial else 160
        try:
            return await self.client.responses.create(
                model=model,
                instructions=SYSTEM_PROMPT,
                input=self._user_input(text, context, partial=partial),
                max_output_tokens=max_output_tokens,
            )
        except BadRequestError as exc:
            if model != settings.openai_fallback_model and "model" in str(exc).lower():
                return await self.client.responses.create(
                    model=settings.openai_fallback_model,
                    instructions=SYSTEM_PROMPT,
                    input=self._user_input(text, context, partial=partial),
                    max_output_tokens=max_output_tokens,
                )
            raise

    async def aclose(self) -> None:
        await self.client.close()


def _clean_source(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _clean_translation(text: str) -> str:
    cleaned = (text or "").strip()
    if cleaned in {'""', "''"}:
        return ""
    return cleaned.strip('"').strip()


def _should_translate(text: str) -> bool:
    if not text:
        return False
    normalized = re.sub(r"[^a-zA-Z]", "", text).lower()
    return normalized not in FILLERS


def _is_bad_translation(text: str) -> bool:
    lowered = text.lower()
    return any(pattern.lower() in lowered for pattern in BAD_TRANSLATION_PATTERNS)


def _fallback_fragment_translation(text: str) -> str:
    return text
