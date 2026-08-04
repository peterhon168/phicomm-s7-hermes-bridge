from __future__ import annotations

from datetime import UTC, datetime

from .config import AppConfig
from .identity import IdentityMatcher
from .models import IngestResult
from .parser import MeasurementParser
from .storage import MeasurementStore


class MeasurementService:
    def __init__(self, config: AppConfig):
        self.config = config
        self.parser = MeasurementParser(config.processing)
        self.matcher = IdentityMatcher(config.people, config.processing)
        self.store = MeasurementStore(config)

    def ingest(
        self,
        topic: str,
        payload: bytes | str,
        received_at: datetime | None = None,
    ) -> list[IngestResult]:
        received = received_at or datetime.now(UTC)
        measurements = self.parser.parse(topic, payload, received)
        results: list[IngestResult] = []
        for measurement in sorted(measurements, key=lambda item: item.measured_at):
            baselines = self.store.recent_baselines(
                self.config.processing.recent_baseline_count
            )
            assignment = self.matcher.assign(measurement.weight_kg, baselines)
            results.append(self.store.record(measurement, assignment))
        self.store.finalize_expired(received)
        return results

    def ingest_pai(
        self,
        record: dict[str, object],
        received_at: datetime | None = None,
        *,
        device_id: str = "pai-s7",
    ) -> IngestResult:
        """Ingest one normalized Pai Health history row."""

        received = received_at or datetime.now(UTC)
        measurement = self.parser.parse_pai(record, received, device_id=device_id)
        baselines = self.store.recent_baselines(
            self.config.processing.recent_baseline_count
        )
        assignment = self.matcher.assign(measurement.weight_kg, baselines)
        result = self.store.record(measurement, assignment)
        self.store.finalize_expired(received)
        return result

    def maintenance(self, now: datetime | None = None) -> int:
        return self.store.finalize_expired(now)

    def rebuild_sessions(self, now: datetime | None = None) -> int:
        return self.store.rebuild_sessions(now)

    def stats(self) -> dict[str, object]:
        return self.store.stats()

    def close(self) -> None:
        self.store.close()

    def __enter__(self) -> "MeasurementService":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
