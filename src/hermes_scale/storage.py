from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import sqlite3
import statistics
import tempfile
import threading
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .config import AppConfig
from .models import Assignment, IngestResult, Measurement


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS people (
    person_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    min_weight_kg REAL NOT NULL,
    max_weight_kg REAL NOT NULL,
    initial_weight_kg REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_measurements (
    event_id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    topic TEXT NOT NULL,
    measured_at TEXT NOT NULL,
    received_at TEXT NOT NULL,
    timestamp_source TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    raw_weight TEXT NOT NULL,
    weight_kg REAL NOT NULL,
    raw_bodyfat TEXT,
    bodyfat_pct REAL,
    person_id TEXT REFERENCES people(person_id),
    assignment_status TEXT NOT NULL,
    assignment_reason TEXT NOT NULL,
    assignment_confidence REAL NOT NULL,
    raw_payload TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_raw_measured_at
ON raw_measurements(measured_at);

CREATE INDEX IF NOT EXISTS idx_raw_person
ON raw_measurements(person_id, measured_at);

CREATE INDEX IF NOT EXISTS idx_raw_device_received
ON raw_measurements(device_id, received_at);

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    person_id TEXT NOT NULL REFERENCES people(person_id),
    device_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    sample_count INTEGER NOT NULL,
    accepted_count INTEGER NOT NULL,
    weight_mean_kg REAL NOT NULL,
    weight_median_kg REAL NOT NULL,
    weight_min_kg REAL NOT NULL,
    weight_max_kg REAL NOT NULL,
    bodyfat_mean_pct REAL,
    bodyfat_median_pct REAL,
    status TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_person_time
ON sessions(person_id, ended_at);

CREATE TABLE IF NOT EXISTS session_samples (
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    event_id TEXT PRIMARY KEY REFERENCES raw_measurements(event_id),
    accepted INTEGER NOT NULL
);
"""


class MeasurementStore:
    """SQLite is canonical; files below the storage root are reproducible exports."""

    def __init__(self, config: AppConfig):
        self.config = config
        self.root = config.storage.root
        self.root.mkdir(parents=True, exist_ok=True)
        self.timezone = ZoneInfo(config.storage.timezone)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            self.root / "scale.sqlite3",
            check_same_thread=False,
            timeout=30,
        )
        self._conn.row_factory = sqlite3.Row
        with self._conn:
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.execute("PRAGMA synchronous = FULL")
            self._conn.executescript(SCHEMA)
            self._migrate_schema_locked()
            for person in config.people:
                self._conn.execute(
                    """
                    INSERT INTO people(
                        person_id, display_name, min_weight_kg,
                        max_weight_kg, initial_weight_kg
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(person_id) DO UPDATE SET
                        display_name=excluded.display_name,
                        min_weight_kg=excluded.min_weight_kg,
                        max_weight_kg=excluded.max_weight_kg,
                        initial_weight_kg=excluded.initial_weight_kg
                    """,
                    (
                        person.id,
                        person.name,
                        person.min_weight_kg,
                        person.max_weight_kg,
                        person.initial_weight_kg,
                    ),
                )

    def _migrate_schema_locked(self) -> None:
        """Add optional Pai fields without rewriting the existing ledger."""

        raw_columns = {
            str(row[1])
            for row in self._conn.execute("PRAGMA table_info(raw_measurements)")
        }
        if "raw_bodyfat" not in raw_columns:
            self._conn.execute("ALTER TABLE raw_measurements ADD COLUMN raw_bodyfat TEXT")
        if "bodyfat_pct" not in raw_columns:
            self._conn.execute("ALTER TABLE raw_measurements ADD COLUMN bodyfat_pct REAL")
        session_columns = {
            str(row[1])
            for row in self._conn.execute("PRAGMA table_info(sessions)")
        }
        if "bodyfat_mean_pct" not in session_columns:
            self._conn.execute("ALTER TABLE sessions ADD COLUMN bodyfat_mean_pct REAL")
        if "bodyfat_median_pct" not in session_columns:
            self._conn.execute("ALTER TABLE sessions ADD COLUMN bodyfat_median_pct REAL")

    def record(self, measurement: Measurement, assignment: Assignment) -> IngestResult:
        raw_payload_json = json.dumps(
            measurement.raw_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        with self._lock:
            with self._conn:
                fallback_duplicate = self._fallback_duplicate_locked(
                    measurement,
                )
                if fallback_duplicate is not None:
                    return self._duplicate_result(fallback_duplicate)
                cursor = self._conn.execute(
                    """
                    INSERT OR IGNORE INTO raw_measurements(
                        event_id, device_id, topic, measured_at, received_at,
                        timestamp_source, source_kind, raw_weight, weight_kg,
                        raw_bodyfat, bodyfat_pct,
                        person_id, assignment_status, assignment_reason,
                        assignment_confidence, raw_payload
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        measurement.event_id,
                        measurement.device_id,
                        measurement.topic,
                        self._iso(measurement.measured_at),
                        self._iso(measurement.received_at),
                        measurement.timestamp_source,
                        measurement.source_kind,
                        measurement.raw_weight,
                        measurement.weight_kg,
                        measurement.raw_bodyfat,
                        measurement.bodyfat_pct,
                        assignment.person_id,
                        assignment.status,
                        assignment.reason,
                        assignment.confidence,
                        raw_payload_json,
                    ),
                )
                if cursor.rowcount == 0:
                    existing = self._conn.execute(
                        """
                        SELECT r.event_id, r.device_id, r.measured_at, r.weight_kg,
                               r.bodyfat_pct, r.person_id, r.assignment_reason,
                               s.session_id
                        FROM raw_measurements r
                        LEFT JOIN session_samples ss ON ss.event_id = r.event_id
                        LEFT JOIN sessions s ON s.session_id = ss.session_id
                        WHERE r.event_id = ?
                        """,
                        (measurement.event_id,),
                    ).fetchone()
                    if existing is None:
                        raise RuntimeError("duplicate event disappeared during lookup")
                    if (
                        measurement.bodyfat_pct is not None
                        and existing["bodyfat_pct"] is None
                    ):
                        self._conn.execute(
                            """
                            UPDATE raw_measurements
                            SET raw_bodyfat = ?, bodyfat_pct = ?
                            WHERE event_id = ?
                            """,
                            (
                                measurement.raw_bodyfat,
                                measurement.bodyfat_pct,
                                measurement.event_id,
                            ),
                        )
                        if existing["session_id"] is not None:
                            self._recompute_session_locked(existing["session_id"])
                        existing = self._conn.execute(
                            """
                            SELECT r.event_id, r.device_id, r.measured_at, r.weight_kg,
                                   r.bodyfat_pct, r.person_id, r.assignment_reason,
                                   s.session_id
                            FROM raw_measurements r
                            LEFT JOIN session_samples ss ON ss.event_id = r.event_id
                            LEFT JOIN sessions s ON s.session_id = ss.session_id
                            WHERE r.event_id = ?
                            """,
                            (measurement.event_id,),
                        ).fetchone()
                        self.rebuild_exports()
                    return self._duplicate_result(existing)

                self._finalize_expired_locked(measurement.measured_at)
                session_id: str | None = None
                if assignment.person_id is not None:
                    session_id = self._session_for_locked(measurement, assignment.person_id)
                    self._conn.execute(
                        """
                        INSERT INTO session_samples(session_id, event_id, accepted)
                        VALUES (?, ?, 1)
                        """,
                        (session_id, measurement.event_id),
                    )
                    self._recompute_session_locked(session_id)

            self.rebuild_exports()
            return IngestResult(
                status="recorded",
                event_id=measurement.event_id,
                device_id=measurement.device_id,
                measured_at=self._iso(measurement.measured_at),
                weight_kg=measurement.weight_kg,
                person_id=assignment.person_id,
                assignment_reason=assignment.reason,
                session_id=session_id,
                bodyfat_pct=measurement.bodyfat_pct,
            )

    def recent_baselines(self, count: int) -> dict[str, float | None]:
        with self._lock:
            result: dict[str, float | None] = {}
            for person in self.config.people:
                rows = self._conn.execute(
                    """
                    SELECT weight_median_kg
                    FROM sessions
                    WHERE person_id = ? AND status = 'finalized'
                    ORDER BY ended_at DESC
                    LIMIT ?
                    """,
                    (person.id, count),
                ).fetchall()
                values = [float(row["weight_median_kg"]) for row in rows]
                result[person.id] = statistics.median(values) if values else None
            return result

    def finalize_expired(self, now: datetime | None = None) -> int:
        current = self._ensure_utc(now or datetime.now(UTC))
        with self._lock:
            with self._conn:
                count = self._finalize_expired_locked(current)
            if count:
                self.rebuild_exports()
            return count

    def rebuild_exports(self) -> None:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM raw_measurements ORDER BY measured_at, event_id"
            ).fetchall()
            raw_by_day: dict[str, list[sqlite3.Row]] = defaultdict(list)
            pending: list[sqlite3.Row] = []
            for row in rows:
                day = (
                    self._parse_iso(row["measured_at"])
                    .astimezone(self.timezone)
                    .date()
                    .isoformat()
                )
                raw_by_day[day].append(row)
                if row["assignment_status"] == "pending":
                    pending.append(row)

            for day, day_rows in raw_by_day.items():
                year, month, date = day.split("-")
                path = self.root / "raw" / year / month / f"{date}.jsonl"
                self._atomic_write(path, self._jsonl(self._raw_export(row) for row in day_rows))

            pending_path = self.root / "pending" / "measurements.jsonl"
            self._atomic_write(
                pending_path,
                self._jsonl(self._raw_export(row) for row in pending),
            )

            for person in self.config.people:
                self._export_person(person.id, person.name)
            self._export_hermes_summary(len(pending))

    def stats(self) -> dict[str, Any]:
        with self._lock:
            raw_count = self._conn.execute("SELECT COUNT(*) FROM raw_measurements").fetchone()[0]
            pending_count = self._conn.execute(
                "SELECT COUNT(*) FROM raw_measurements WHERE assignment_status='pending'"
            ).fetchone()[0]
            session_count = self._conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            review_count = self._conn.execute(
                "SELECT COUNT(*) FROM sessions WHERE status='review'"
            ).fetchone()[0]
            latest = self._conn.execute(
                "SELECT MAX(received_at) FROM raw_measurements"
            ).fetchone()[0]
            return {
                "raw_measurements": raw_count,
                "pending_measurements": pending_count,
                "sessions": session_count,
                "review_sessions": review_count,
                "last_received_at": latest,
            }

    def known_device_ids(self) -> tuple[str, ...]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT device_id FROM raw_measurements ORDER BY device_id"
            ).fetchall()
            return tuple(str(row["device_id"]) for row in rows)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _fallback_duplicate_locked(
        self,
        measurement: Measurement,
    ) -> sqlite3.Row | None:
        if measurement.timestamp_source != "received":
            return None
        cutoff = measurement.received_at - timedelta(
            seconds=self.config.processing.session_window_seconds
        )
        return self._conn.execute(
            """
            SELECT r.event_id, r.device_id, r.measured_at, r.weight_kg,
                   r.bodyfat_pct, r.person_id, r.assignment_reason, s.session_id
            FROM raw_measurements r
            LEFT JOIN session_samples ss ON ss.event_id = r.event_id
            LEFT JOIN sessions s ON s.session_id = ss.session_id
            WHERE r.device_id = ?
              AND r.timestamp_source = 'received'
              AND r.weight_kg = ?
              AND r.received_at BETWEEN ? AND ?
              AND (? = 'sensor' OR r.source_kind = 'sensor')
            ORDER BY r.received_at DESC
            LIMIT 1
            """,
            (
                measurement.device_id,
                measurement.weight_kg,
                self._iso(cutoff),
                self._iso(measurement.received_at),
                measurement.source_kind,
            ),
        ).fetchone()

    @staticmethod
    def _duplicate_result(existing: sqlite3.Row) -> IngestResult:
        return IngestResult(
            status="duplicate",
            event_id=existing["event_id"],
            device_id=existing["device_id"],
            measured_at=existing["measured_at"],
            weight_kg=float(existing["weight_kg"]),
            person_id=existing["person_id"],
            assignment_reason=existing["assignment_reason"],
            session_id=existing["session_id"],
            bodyfat_pct=(
                float(existing["bodyfat_pct"])
                if existing["bodyfat_pct"] is not None
                else None
            ),
        )

    def _session_for_locked(self, measurement: Measurement, person_id: str) -> str:
        row = self._conn.execute(
            """
            SELECT session_id, started_at, ended_at
            FROM sessions
            WHERE person_id = ? AND device_id = ? AND status = 'open'
            ORDER BY ended_at DESC
            LIMIT 1
            """,
            (person_id, measurement.device_id),
        ).fetchone()
        if row is not None:
            ended_at = self._parse_iso(row["ended_at"])
            delta = measurement.measured_at - ended_at
            if timedelta(0) <= delta <= timedelta(
                seconds=self.config.processing.session_window_seconds
            ):
                self._conn.execute(
                    "UPDATE sessions SET ended_at = ? WHERE session_id = ?",
                    (self._iso(measurement.measured_at), row["session_id"]),
                )
                return str(row["session_id"])

        session_id = hashlib.sha256(
            f"{person_id}|{measurement.device_id}|{measurement.event_id}".encode("utf-8")
        ).hexdigest()[:24]
        self._conn.execute(
            """
            INSERT INTO sessions(
                session_id, person_id, device_id, started_at, ended_at,
                sample_count, accepted_count, weight_mean_kg,
                weight_median_kg, weight_min_kg, weight_max_kg,
                bodyfat_mean_pct, bodyfat_median_pct, status
            ) VALUES (?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?, NULL, NULL, 'open')
            """,
            (
                session_id,
                person_id,
                measurement.device_id,
                self._iso(measurement.measured_at),
                self._iso(measurement.measured_at),
                measurement.weight_kg,
                measurement.weight_kg,
                measurement.weight_kg,
                measurement.weight_kg,
            ),
        )
        return session_id

    def _recompute_session_locked(self, session_id: str) -> None:
        rows = self._conn.execute(
            """
            SELECT r.event_id, r.weight_kg, r.bodyfat_pct
            FROM session_samples ss
            JOIN raw_measurements r ON r.event_id = ss.event_id
            WHERE ss.session_id = ?
            ORDER BY r.measured_at, r.event_id
            """,
            (session_id,),
        ).fetchall()
        weights = [float(row["weight_kg"]) for row in rows]
        if not weights:
            raise RuntimeError(f"session has no samples: {session_id}")
        median = float(statistics.median(weights))
        if len(weights) >= 3:
            accepted = [
                abs(weight - median) <= self.config.processing.outlier_threshold_kg
                for weight in weights
            ]
            if sum(accepted) < len(weights) // 2 + 1:
                accepted = [False] * len(weights)
        else:
            accepted = [True] * len(weights)
        accepted_weights = [weight for weight, keep in zip(weights, accepted, strict=True) if keep]
        bodyfat_values = [
            float(row["bodyfat_pct"])
            for row, keep in zip(rows, accepted, strict=True)
            if keep and row["bodyfat_pct"] is not None
        ]
        for row, keep in zip(rows, accepted, strict=True):
            self._conn.execute(
                "UPDATE session_samples SET accepted = ? WHERE event_id = ?",
                (1 if keep else 0, row["event_id"]),
            )
        if accepted_weights:
            mean_value = round(statistics.fmean(accepted_weights), 3)
            median_value = round(statistics.median(accepted_weights), 3)
            minimum_value = min(accepted_weights)
            maximum_value = max(accepted_weights)
        else:
            # Metrics remain audit aids only; status becomes review at expiry and
            # review sessions are excluded from every Hermes/daily summary.
            mean_value = round(statistics.fmean(weights), 3)
            median_value = round(statistics.median(weights), 3)
            minimum_value = min(weights)
            maximum_value = max(weights)
        bodyfat_mean = round(statistics.fmean(bodyfat_values), 3) if bodyfat_values else None
        bodyfat_median = round(statistics.median(bodyfat_values), 3) if bodyfat_values else None
        self._conn.execute(
            """
            UPDATE sessions SET
                sample_count = ?,
                accepted_count = ?,
                weight_mean_kg = ?,
                weight_median_kg = ?,
                weight_min_kg = ?,
                weight_max_kg = ?
                ,bodyfat_mean_pct = ?
                ,bodyfat_median_pct = ?
            WHERE session_id = ?
            """,
            (
                len(weights),
                len(accepted_weights),
                mean_value,
                median_value,
                minimum_value,
                maximum_value,
                bodyfat_mean,
                bodyfat_median,
                session_id,
            ),
        )

    def _finalize_expired_locked(self, now: datetime) -> int:
        cutoff = now - timedelta(seconds=self.config.processing.session_window_seconds)
        cursor = self._conn.execute(
            """
            UPDATE sessions
            SET status = CASE
                WHEN accepted_count > 0 THEN 'finalized'
                ELSE 'review'
            END
            WHERE status = 'open' AND ended_at <= ?
            """,
            (self._iso(cutoff),),
        )
        return cursor.rowcount

    def _export_person(self, person_id: str, person_name: str) -> None:
        rows = self._conn.execute(
            """
            SELECT * FROM sessions
            WHERE person_id = ?
            ORDER BY started_at, session_id
            """,
            (person_id,),
        ).fetchall()
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(
            [
                "session_id",
                "started_at",
                "ended_at",
                "status",
                "sample_count",
                "accepted_count",
                "weight_mean_kg",
                "weight_median_kg",
                "weight_min_kg",
                "weight_max_kg",
                "bodyfat_mean_pct",
                "bodyfat_median_pct",
                "device_id",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row["session_id"],
                    row["started_at"],
                    row["ended_at"],
                    row["status"],
                    row["sample_count"],
                    row["accepted_count"],
                    f'{row["weight_mean_kg"]:.3f}',
                    f'{row["weight_median_kg"]:.3f}',
                    f'{row["weight_min_kg"]:.3f}',
                    f'{row["weight_max_kg"]:.3f}',
                    (
                        f'{row["bodyfat_mean_pct"]:.3f}'
                        if row["bodyfat_mean_pct"] is not None
                        else ""
                    ),
                    (
                        f'{row["bodyfat_median_pct"]:.3f}'
                        if row["bodyfat_median_pct"] is not None
                        else ""
                    ),
                    row["device_id"],
                ]
            )
        user_root = self.root / "users" / person_id
        self._atomic_write(user_root / "measurements.csv", output.getvalue())

        finalized = [row for row in rows if row["status"] == "finalized"]
        daily_values: dict[str, list[float]] = defaultdict(list)
        daily_bodyfat_values: dict[str, list[float]] = defaultdict(list)
        for row in finalized:
            day = self._parse_iso(row["ended_at"]).astimezone(self.timezone).date().isoformat()
            daily_values[day].append(float(row["weight_mean_kg"]))
            if row["bodyfat_mean_pct"] is not None:
                daily_bodyfat_values[day].append(float(row["bodyfat_mean_pct"]))
        daily = [
            self._daily_export(day, values, daily_bodyfat_values.get(day, []))
            for day, values in sorted(daily_values.items())
        ]
        summary = {
            "schema_version": 1,
            "person_id": person_id,
            "person_name": person_name,
            "timezone": self.config.storage.timezone,
            "generated_at": self._iso(datetime.now(UTC)),
            "days": daily,
        }
        self._atomic_write(
            user_root / "daily-summary.json",
            json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )

    def _export_hermes_summary(self, pending_count: int) -> None:
        review_count = self._conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE status = 'review'"
        ).fetchone()[0]
        people: dict[str, dict[str, Any]] = {}
        for person in self.config.people:
            latest = self._conn.execute(
                """
                SELECT session_id, started_at, ended_at, sample_count,
                       accepted_count, weight_mean_kg, weight_median_kg,
                       weight_min_kg, weight_max_kg, bodyfat_mean_pct,
                       bodyfat_median_pct, device_id
                FROM sessions
                WHERE person_id = ? AND status = 'finalized'
                ORDER BY ended_at DESC, session_id DESC
                LIMIT 1
                """,
                (person.id,),
            ).fetchone()
            session_count = self._conn.execute(
                """
                SELECT COUNT(*) FROM sessions
                WHERE person_id = ? AND status = 'finalized'
                """,
                (person.id,),
            ).fetchone()[0]
            latest_export = None
            if latest is not None:
                latest_export = {
                    "session_id": latest["session_id"],
                    "started_at": latest["started_at"],
                    "ended_at": latest["ended_at"],
                    "sample_count": latest["sample_count"],
                    "accepted_count": latest["accepted_count"],
                    "weight_mean_kg": latest["weight_mean_kg"],
                    "weight_median_kg": latest["weight_median_kg"],
                    "weight_min_kg": latest["weight_min_kg"],
                    "weight_max_kg": latest["weight_max_kg"],
                    "bodyfat_mean_pct": latest["bodyfat_mean_pct"],
                    "bodyfat_median_pct": latest["bodyfat_median_pct"],
                    "device_id": latest["device_id"],
                }
            people[person.id] = {
                "name": person.name,
                "finalized_session_count": session_count,
                "latest": latest_export,
                "measurements_csv": f"users/{person.id}/measurements.csv",
                "daily_summary_json": f"users/{person.id}/daily-summary.json",
            }
        summary = {
            "schema_version": 1,
            "generated_at": self._iso(datetime.now(UTC)),
            "timezone": self.config.storage.timezone,
            "pending_measurements": pending_count,
            "review_sessions": review_count,
            "people": people,
        }
        self._atomic_write(
            self.root / "hermes-summary.json",
            json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )

    @staticmethod
    def _raw_export(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "event_id": row["event_id"],
            "device_id": row["device_id"],
            "topic": row["topic"],
            "source_kind": row["source_kind"],
            "measured_at": row["measured_at"],
            "received_at": row["received_at"],
            "timestamp_source": row["timestamp_source"],
            "raw_weight": row["raw_weight"],
            "weight_kg": row["weight_kg"],
            "raw_bodyfat": row["raw_bodyfat"],
            "bodyfat_pct": row["bodyfat_pct"],
            "person_id": row["person_id"],
            "assignment_status": row["assignment_status"],
            "assignment_reason": row["assignment_reason"],
            "assignment_confidence": row["assignment_confidence"],
            "raw_payload": json.loads(row["raw_payload"]),
        }

    @staticmethod
    def _daily_export(
        day: str,
        weights: list[float],
        bodyfat_values: list[float],
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "date": day,
            "session_count": len(weights),
            "weight_mean_kg": round(statistics.fmean(weights), 3),
            "weight_min_kg": round(min(weights), 3),
            "weight_max_kg": round(max(weights), 3),
        }
        if bodyfat_values:
            result.update(
                {
                    "bodyfat_mean_pct": round(statistics.fmean(bodyfat_values), 3),
                    "bodyfat_min_pct": round(min(bodyfat_values), 3),
                    "bodyfat_max_pct": round(max(bodyfat_values), 3),
                }
            )
        else:
            result.update(
                {
                    "bodyfat_mean_pct": None,
                    "bodyfat_min_pct": None,
                    "bodyfat_max_pct": None,
                }
            )
        return result

    @staticmethod
    def _jsonl(items: Iterable[dict[str, Any]]) -> str:
        lines = [
            json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            for item in items
        ]
        return "\n".join(lines) + ("\n" if lines else "")

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()

    @staticmethod
    def _iso(value: datetime) -> str:
        return MeasurementStore._ensure_utc(value).isoformat(timespec="seconds")

    @staticmethod
    def _parse_iso(value: str) -> datetime:
        return datetime.fromisoformat(value).astimezone(UTC)

    @staticmethod
    def _ensure_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("datetime must be timezone-aware")
        return value.astimezone(UTC)
