from __future__ import annotations

from pydantic import BaseModel


class UiStartRequest(BaseModel):
    type: str
    url: str


class SegmentEvent(BaseModel):
    type: str = "segment"
    segment_id: str
    seq: int
    phase: str
    text: str
    stt_ms: int = 0
    trans_ms: int = 0
    total_ms: int = 0
