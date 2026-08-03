from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class Measurement:
    event_id: str
    device_id: str
    topic: str
    measured_at: datetime
    received_at: datetime
    timestamp_source: str
    raw_weight: str
    weight_kg: float
    raw_payload: dict[str, Any]
    source_kind: str
    bodyfat_pct: float | None = None
    raw_bodyfat: str | None = None


@dataclass(frozen=True)
class Assignment:
    person_id: str | None
    person_name: str | None
    confidence: float
    reason: str

    @property
    def status(self) -> str:
        return "assigned" if self.person_id is not None else "pending"


@dataclass(frozen=True)
class IngestResult:
    status: str
    event_id: str
    device_id: str
    measured_at: str
    weight_kg: float
    person_id: str | None
    assignment_reason: str
    session_id: str | None
    bodyfat_pct: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
