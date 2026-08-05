from __future__ import annotations

import csv
import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from hermes_scale.service import MeasurementService

from tests.helpers import make_config


class MeasurementServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = make_config(self.root)
        self.service = MeasurementService(self.config)
        self.base = datetime(2026, 8, 3, 8, 0, tzinfo=UTC)
        self.mac = "1234567890ab"

    def tearDown(self) -> None:
        self.service.close()
        self.temp.cleanup()

    def ingest(self, weight: float, seconds: int, *, received_lag: int = 1):
        measured = self.base + timedelta(seconds=seconds)
        payload = json.dumps(
            {
                "mac": self.mac,
                "weight": weight,
                "time": int(measured.timestamp()),
            }
        )
        return self.service.ingest(
            f"device/zs7/{self.mac}/sensor",
            payload,
            measured + timedelta(seconds=received_lag),
        )[0]

    def test_two_people_average_dedupe_pending_and_exports(self) -> None:
        first = self.ingest(80.0, 0)
        duplicate = self.ingest(80.0, 0)
        self.ingest(80.2, 60)
        self.ingest(80.1, 120)
        self.ingest(90.0, 150)
        mother = self.ingest(55.0, 200)
        pending = self.ingest(67.0, 240)

        self.assertEqual(first.person_id, "person_a")
        self.assertEqual(duplicate.status, "duplicate")
        self.assertEqual(mother.person_id, "person_b")
        self.assertIsNone(pending.person_id)

        self.service.maintenance(self.base + timedelta(minutes=10))
        stats = self.service.stats()
        self.assertEqual(stats["raw_measurements"], 6)
        self.assertEqual(stats["pending_measurements"], 1)
        self.assertEqual(stats["sessions"], 2)

        with (self.root / "users" / "person_a" / "measurements.csv").open(
            encoding="utf-8", newline=""
        ) as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "finalized")
        self.assertEqual(rows[0]["sample_count"], "4")
        self.assertEqual(rows[0]["accepted_count"], "3")
        self.assertAlmostEqual(float(rows[0]["weight_mean_kg"]), 80.1, places=3)

        summary = json.loads(
            (self.root / "users" / "person_a" / "daily-summary.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(summary["days"][0]["session_count"], 1)
        self.assertAlmostEqual(summary["days"][0]["weight_mean_kg"], 80.1, places=3)

        hermes_summary = json.loads(
            (self.root / "hermes-summary.json").read_text(encoding="utf-8")
        )
        self.assertEqual(hermes_summary["pending_measurements"], 1)
        self.assertAlmostEqual(
            hermes_summary["people"]["person_a"]["latest"]["weight_mean_kg"],
            80.1,
            places=3,
        )
        self.assertAlmostEqual(
            hermes_summary["people"]["person_b"]["latest"]["weight_mean_kg"],
            55.0,
            places=3,
        )

        pending_lines = (
            self.root / "pending" / "measurements.jsonl"
        ).read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(pending_lines), 1)
        self.assertEqual(json.loads(pending_lines[0])["weight_kg"], 67.0)

    def test_history_replay_is_idempotent(self) -> None:
        payload = json.dumps(
            {
                "mac": self.mac,
                "history": {
                    "weight": [8000, 8050],
                    "utc": [
                        int(self.base.timestamp()),
                        int((self.base + timedelta(minutes=1)).timestamp()),
                    ],
                },
            }
        )
        first = self.service.ingest(
            f"device/zs7/{self.mac}/state",
            payload,
            self.base + timedelta(minutes=5),
        )
        second = self.service.ingest(
            f"device/zs7/{self.mac}/state",
            payload,
            self.base + timedelta(minutes=6),
        )
        self.assertEqual([result.status for result in first], ["recorded", "recorded"])
        self.assertEqual([result.status for result in second], ["duplicate", "duplicate"])
        self.assertEqual(self.service.stats()["raw_measurements"], 2)

    def test_rebuild_sessions_rejoins_out_of_order_pai_history(self) -> None:
        records = [
            {"measureId": 3, "weight": 80.2, "createTime": int((self.base + timedelta(seconds=26)).timestamp() * 1000)},
            {"measureId": 1, "weight": 80.0, "createTime": int(self.base.timestamp() * 1000)},
            {"measureId": 2, "weight": 80.1, "createTime": int((self.base + timedelta(seconds=13)).timestamp() * 1000)},
        ]
        for record in records:
            self.service.ingest_pai(record, self.base + timedelta(minutes=1))

        self.service.maintenance(self.base + timedelta(minutes=10))
        self.assertEqual(self.service.stats()["raw_measurements"], 3)
        self.assertEqual(self.service.stats()["sessions"], 3)

        rebuilt = self.service.rebuild_sessions(self.base + timedelta(minutes=10))
        self.assertEqual(rebuilt, 1)
        self.assertEqual(self.service.stats()["sessions"], 1)

        with (self.root / "users" / "person_a" / "measurements.csv").open(
            encoding="utf-8", newline=""
        ) as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(rows[0]["status"], "finalized")
        self.assertEqual(rows[0]["sample_count"], "3")
        self.assertEqual(rows[0]["accepted_count"], "3")
        self.assertAlmostEqual(float(rows[0]["weight_mean_kg"]), 80.1, places=3)

    def test_pai_batch_can_defer_finalization_until_all_rows_are_ingested(self) -> None:
        received = self.base + timedelta(minutes=10)
        records = [
            {"measureId": 11, "weight": 80.0, "createTime": int(self.base.timestamp() * 1000)},
            {"measureId": 12, "weight": 80.1, "createTime": int((self.base + timedelta(seconds=13)).timestamp() * 1000)},
            {"measureId": 13, "weight": 80.2, "createTime": int((self.base + timedelta(seconds=26)).timestamp() * 1000)},
        ]
        for record in records:
            result = self.service.ingest_pai(record, received, finalize=False)
            self.assertEqual(result.status, "recorded")

        self.service.maintenance(received)
        with (self.root / "users" / "person_a" / "measurements.csv").open(
            encoding="utf-8", newline=""
        ) as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sample_count"], "3")
        self.assertEqual(rows[0]["accepted_count"], "3")

    def test_sensor_then_history_is_one_measurement(self) -> None:
        sensor = self.ingest(80.2, 0)
        payload = json.dumps(
            {
                "mac": self.mac,
                "history": {
                    "weight": [8020],
                    "utc": [int(self.base.timestamp())],
                },
            }
        )
        history = self.service.ingest(
            f"device/zs7/{self.mac}/state",
            payload,
            self.base + timedelta(minutes=1),
        )[0]
        self.assertEqual(sensor.status, "recorded")
        self.assertEqual(history.status, "duplicate")
        self.assertEqual(self.service.stats()["raw_measurements"], 1)

    def test_unknown_time_sensor_retry_inside_session_is_duplicate(self) -> None:
        payload = json.dumps({"mac": self.mac, "weight": "80.20", "time": 0})
        first = self.service.ingest(
            f"device/zs7/{self.mac}/sensor",
            payload,
            self.base,
        )[0]
        second = self.service.ingest(
            f"device/zs7/{self.mac}/sensor",
            payload,
            self.base + timedelta(minutes=1),
        )[0]
        self.assertEqual(first.status, "recorded")
        self.assertEqual(second.status, "duplicate")
        self.assertEqual(first.event_id, second.event_id)
        self.assertEqual(self.service.stats()["raw_measurements"], 1)

    def test_unknown_time_sensor_and_history_are_deduplicated(self) -> None:
        sensor_payload = json.dumps(
            {"mac": self.mac, "weight": "80.20", "time": 0}
        )
        sensor = self.service.ingest(
            f"device/zs7/{self.mac}/sensor",
            sensor_payload,
            self.base,
        )[0]
        history_payload = json.dumps(
            {
                "mac": self.mac,
                "history": {"weight": [8020], "utc": [0]},
            }
        )
        history = self.service.ingest(
            f"device/zs7/{self.mac}/state",
            history_payload,
            self.base + timedelta(minutes=1),
        )[0]
        self.assertEqual(sensor.status, "recorded")
        self.assertEqual(history.status, "duplicate")
        self.assertEqual(self.service.stats()["raw_measurements"], 1)

    def test_session_without_majority_consensus_requires_review(self) -> None:
        self.ingest(80.0, 0)
        self.ingest(82.0, 30)
        self.ingest(84.0, 60)
        self.ingest(86.0, 90)
        self.service.maintenance(self.base + timedelta(minutes=10))

        with (self.root / "users" / "person_a" / "measurements.csv").open(
            encoding="utf-8", newline=""
        ) as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(rows[0]["status"], "review")
        self.assertEqual(rows[0]["accepted_count"], "0")
        self.assertEqual(self.service.stats()["review_sessions"], 1)

        summary = json.loads(
            (self.root / "hermes-summary.json").read_text(encoding="utf-8")
        )
        self.assertEqual(summary["review_sessions"], 1)
        self.assertIsNone(summary["people"]["person_a"]["latest"])
        daily = json.loads(
            (self.root / "users" / "person_a" / "daily-summary.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(daily["days"], [])


if __name__ == "__main__":
    unittest.main()
