from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from .config import ProcessingConfig
from .models import Measurement


MAC_RE = re.compile(r"^[0-9a-f]{12}$")
EARLIEST_DEVICE_TIME = datetime(2017, 1, 1, tzinfo=UTC)


class PayloadError(ValueError):
    """Raised when an MQTT payload cannot be safely interpreted."""


class MeasurementParser:
    def __init__(self, config: ProcessingConfig):
        self.config = config

    def parse(
        self,
        topic: str,
        payload: bytes | str,
        received_at: datetime | None = None,
    ) -> list[Measurement]:
        received = self._ensure_utc(received_at or datetime.now(UTC))
        try:
            raw_text = payload.decode("utf-8") if isinstance(payload, bytes) else payload
            body = json.loads(raw_text)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PayloadError("payload is not valid UTF-8 JSON") from exc
        if not isinstance(body, dict):
            raise PayloadError("payload root must be a JSON object")

        device_id, kind = self._topic_parts(topic)
        payload_mac = body.get("mac")
        if payload_mac is not None:
            normalized_payload_mac = self._normalize_mac(str(payload_mac))
            if normalized_payload_mac != device_id:
                raise PayloadError("topic MAC and payload MAC do not match")

        if kind == "sensor":
            return [
                self._measurement(
                    topic=topic,
                    body=body,
                    device_id=device_id,
                    raw_weight=body.get("weight"),
                    raw_time=body.get("time"),
                    received_at=received,
                    source_kind="sensor",
                    source_index=None,
                )
            ]
        if kind == "state":
            return self._history_measurements(topic, body, device_id, received)
        raise PayloadError(f"unsupported zS7 topic kind: {kind}")

    def parse_pai(
        self,
        record: dict[str, Any],
        received_at: datetime | None = None,
        *,
        device_id: str = "pai-s7",
    ) -> Measurement:
        """Normalize one Pai Health history record.

        Pai returns a stable ``measureId`` for history rows.  That ID is used
        as the event identity so polling the recent-record window repeatedly is
        idempotent.  Body-fat is optional because some S7 rows contain weight
        only (for example before the cloud finishes calculating the detail).
        """

        if not isinstance(record, dict):
            raise PayloadError("Pai record must be an object")
        received = self._ensure_utc(received_at or datetime.now(UTC))
        raw_weight = record.get("weight")
        raw_weight_text, weight_kg = self._normalize_weight(raw_weight)
        raw_bodyfat = record.get("bfr", record.get("bodyfat_pct"))
        bodyfat_text, bodyfat_pct = self._normalize_bodyfat(raw_bodyfat)
        measured_at, timestamp_source = self._normalize_pai_time(
            record.get("createTime", record.get("timestamp")),
            received,
        )
        measure_id = record.get("measureId", record.get("rawDataId"))
        if measure_id is None or str(measure_id).strip() == "":
            identity_material: dict[str, Any] = {
                "device_id": device_id,
                "weight_kg": f"{weight_kg:.3f}",
                "bodyfat_pct": bodyfat_pct,
                "measured_at": measured_at.isoformat(),
                "record": record,
            }
        else:
            identity_material = {
                "device_id": device_id,
                "measure_id": str(measure_id),
            }
        event_id = hashlib.sha256(
            json.dumps(identity_material, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()
        return Measurement(
            event_id=event_id,
            device_id=device_id,
            topic="pai/balance/history",
            measured_at=measured_at,
            received_at=received,
            timestamp_source=timestamp_source,
            raw_weight=raw_weight_text,
            weight_kg=weight_kg,
            raw_payload=dict(record),
            source_kind="pai_history",
            bodyfat_pct=bodyfat_pct,
            raw_bodyfat=bodyfat_text,
        )

    def _history_measurements(
        self,
        topic: str,
        body: dict[str, Any],
        device_id: str,
        received_at: datetime,
    ) -> list[Measurement]:
        history = body.get("history")
        if history is None:
            return []
        if not isinstance(history, dict):
            raise PayloadError("state.history must be an object")
        weights = history.get("weight", [])
        times = history.get("utc", [])
        if not isinstance(weights, list) or not isinstance(times, list):
            raise PayloadError("state.history weight and utc must be arrays")
        if len(weights) != len(times):
            raise PayloadError("state.history weight and utc lengths differ")
        measurements = [
            self._measurement(
                topic=topic,
                body=body,
                device_id=device_id,
                raw_weight=raw_weight,
                raw_time=raw_time,
                received_at=received_at,
                source_kind="history",
                source_index=index,
            )
            for index, (raw_weight, raw_time) in enumerate(zip(weights, times, strict=True))
        ]
        return sorted(measurements, key=lambda item: item.measured_at)

    def _measurement(
        self,
        *,
        topic: str,
        body: dict[str, Any],
        device_id: str,
        raw_weight: Any,
        raw_time: Any,
        received_at: datetime,
        source_kind: str,
        source_index: int | None,
    ) -> Measurement:
        raw_weight_text, weight_kg = self._normalize_weight(raw_weight)
        measured_at, timestamp_source = self._normalize_time(raw_time, received_at)
        # A stable measurement is normally emitted once on /sensor and can later
        # reappear in /state history. Canonical identity collapses both forms.
        if timestamp_source == "device":
            identity_material = {
                "device_id": device_id,
                "weight_kg": f"{weight_kg:.3f}",
                "measured_at": measured_at.isoformat(),
            }
        elif source_kind == "history":
            # time=0 provides no real measurement identity. Make an exact state
            # snapshot replay idempotent while retaining equal entries by index.
            history_snapshot = json.dumps(
                body.get("history"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            identity_material = {
                "device_id": device_id,
                "source_kind": source_kind,
                "source_index": source_index,
                "weight_kg": f"{weight_kg:.3f}",
                "history_snapshot": hashlib.sha256(
                    history_snapshot.encode("utf-8")
                ).hexdigest(),
            }
        else:
            # Unknown-time live measurements are deduplicated within the session
            # window by MeasurementStore; receipt time keeps later sessions apart.
            identity_material = {
                "device_id": device_id,
                "source_kind": source_kind,
                "weight_kg": f"{weight_kg:.3f}",
                "received_at": received_at.isoformat(),
            }
        event_id = hashlib.sha256(
            json.dumps(identity_material, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        raw_payload = dict(body)
        if source_index is not None:
            raw_payload = {
                "history_index": source_index,
                "history_weight": raw_weight,
                "history_time": raw_time,
                "state": body,
            }
        return Measurement(
            event_id=event_id,
            device_id=device_id,
            topic=topic,
            measured_at=measured_at,
            received_at=received_at,
            timestamp_source=timestamp_source,
            raw_weight=raw_weight_text,
            weight_kg=weight_kg,
            raw_payload=raw_payload,
            source_kind=source_kind,
        )

    def _normalize_weight(self, raw_weight: Any) -> tuple[str, float]:
        if isinstance(raw_weight, bool) or raw_weight is None:
            raise PayloadError("weight is missing or not numeric")
        try:
            raw_decimal = Decimal(str(raw_weight).strip())
        except (InvalidOperation, AttributeError) as exc:
            raise PayloadError("weight is not numeric") from exc
        if not raw_decimal.is_finite() or raw_decimal <= 0:
            raise PayloadError("weight must be finite and positive")

        if self.config.weight_divisor == "auto":
            direct = float(raw_decimal)
            scaled = float(raw_decimal / Decimal(100))
            if self._valid_weight(direct):
                normalized = direct
            elif self._valid_weight(scaled):
                normalized = scaled
            else:
                raise PayloadError("weight is outside the configured valid range")
        else:
            normalized = float(raw_decimal / Decimal(str(self.config.weight_divisor)))
            if not self._valid_weight(normalized):
                raise PayloadError("normalized weight is outside the configured valid range")
        return format(raw_decimal, "f"), round(normalized, 3)

    @staticmethod
    def _normalize_bodyfat(raw_bodyfat: Any) -> tuple[str | None, float | None]:
        if raw_bodyfat is None or raw_bodyfat == "":
            return None, None
        if isinstance(raw_bodyfat, bool):
            raise PayloadError("body-fat is not numeric")
        try:
            value = Decimal(str(raw_bodyfat).strip())
        except (InvalidOperation, AttributeError) as exc:
            raise PayloadError("body-fat is not numeric") from exc
        if not value.is_finite() or not Decimal("0") <= value <= Decimal("100"):
            raise PayloadError("body-fat must be between 0 and 100")
        return format(value, "f"), round(float(value), 3)

    @staticmethod
    def _normalize_pai_time(
        raw_time: Any,
        received_at: datetime,
    ) -> tuple[datetime, str]:
        if raw_time is None or raw_time == "":
            return received_at, "received"
        if isinstance(raw_time, datetime):
            if raw_time.tzinfo is None:
                return received_at, "received"
            return raw_time.astimezone(UTC), "device"
        text = str(raw_time).strip()
        try:
            numeric = Decimal(text)
            if not numeric.is_finite():
                raise ValueError
            # Pai uses Unix milliseconds for createTime; tolerate seconds too.
            seconds = float(numeric / Decimal(1000)) if abs(numeric) > 100_000_000_000 else float(numeric)
            candidate = datetime.fromtimestamp(seconds, tz=UTC)
        except (InvalidOperation, TypeError, ValueError, OSError, OverflowError):
            try:
                candidate = datetime.fromisoformat(text.replace("Z", "+00:00"))
                if candidate.tzinfo is None:
                    return received_at, "received"
                candidate = candidate.astimezone(UTC)
            except ValueError:
                return received_at, "received"
        if candidate < EARLIEST_DEVICE_TIME or candidate > received_at + timedelta(days=7):
            return received_at, "received"
        return candidate, "device"

    def _valid_weight(self, value: float) -> bool:
        return self.config.min_valid_weight_kg <= value <= self.config.max_valid_weight_kg

    @staticmethod
    def _normalize_time(raw_time: Any, received_at: datetime) -> tuple[datetime, str]:
        if isinstance(raw_time, bool):
            return received_at, "received"
        try:
            timestamp = int(str(raw_time).strip())
            candidate = datetime.fromtimestamp(timestamp, tz=UTC)
        except (TypeError, ValueError, OSError, OverflowError):
            return received_at, "received"
        if candidate < EARLIEST_DEVICE_TIME or candidate > received_at + timedelta(days=1):
            return received_at, "received"
        return candidate, "device"

    @staticmethod
    def _topic_parts(topic: str) -> tuple[str, str]:
        parts = topic.split("/")
        if len(parts) != 4 or parts[:2] != ["device", "zs7"]:
            raise PayloadError(f"unexpected topic: {topic}")
        return MeasurementParser._normalize_mac(parts[2]), parts[3]

    @staticmethod
    def _normalize_mac(value: str) -> str:
        normalized = value.strip().lower().replace(":", "").replace("-", "")
        if not MAC_RE.fullmatch(normalized):
            raise PayloadError(f"invalid device MAC: {value!r}")
        return normalized

    @staticmethod
    def _ensure_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            raise PayloadError("received_at must be timezone-aware")
        return value.astimezone(UTC)
