from __future__ import annotations

import httpx

from .settings import settings


GLOSSARY = {
    "guidance": "가이던스 또는 실적 전망",
    "gross margin": "매출총이익률",
    "operating margin": "영업이익률",
    "diluted eps": "희석 EPS",
    "buyback": "자사주 매입",
    "capex": "자본적 지출",
    "free cash flow": "잉여현금흐름",
    "year over year": "전년 동기 대비",
    "quarter over quarter": "전분기 대비",
}


def _glossary_text() -> str:
    return "\n".join(f"- {source}: {target}" for source, target in GLOSSARY.items())


SYSTEM_PROMPT = f"""You are a real-time financial interpreter.
Translate English earnings-call speech into Korean.
Return Korean only.
Use the glossary when relevant:
{_glossary_text()}
"""


class Translator:
    def __init__(self) -> None:
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.vllm_timeout),
            trust_env=False,
        )

    async def translate(self, text: str, context: list[str]) -> str:
        if settings.translation_backend == "google":
            response = await self.client.get(
                settings.google_translate_url,
                params={"client": "gtx", "sl": "en", "tl": "ko", "dt": "t", "q": text},
            )
            response.raise_for_status()
            data = response.json()
            return "".join(part[0] for part in data[0] if part and part[0]).strip()

        user_text = text if not context else f"Context: {' '.join(context)}\nCurrent sentence: {text}"
        response = await self.client.post(
            f"{settings.vllm_base_url.rstrip('/')}{settings.vllm_chat_path}",
            json={
                "model": settings.vllm_model_name,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_text},
                ],
                "max_tokens": 256,
                "temperature": 0.1,
            },
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"].strip()

    async def aclose(self) -> None:
        await self.client.aclose()
