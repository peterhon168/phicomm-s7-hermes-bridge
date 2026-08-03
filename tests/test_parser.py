from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from hermes_scale.parser import MeasurementParser, PayloadError

from tests.helpers import make_config


class MeasurementParserTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.config = make_config(Path(self.temp.name))
        self.parser = MeasurementParser(self.config.processing)
        self.now = datetime(2026, 8, 3, 8, 0, tzinfo=UTC)
        self.mac = "1234567890ab"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_sensor_weight_in_kg(self) -> None:
        payload = json.dumps(
            {"mac": self.mac, "weight": "80.25", "time": int(self.now.timestamp())}
        )
        item = self.parser.parse(
            f"device/zs7/{self.mac}/sensor", payload, self.now + timedelta(seconds=1)
        )[0]
        self.assertEqual(item.weight_kg, 80.25)
        self.assertEqual(item.timestamp_source, "device")
        self.assertEqual(item.device_id, self.mac)

    def test_auto_detects_weight_scaled_by_100(self) -> None:
        payload = json.dumps(
            {"mac": self.mac, "weight": 5500, "time": int(self.now.timestamp())}
        )
        item = self.parser.parse(
            f"device/zs7/{self.mac}/sensor", payload, self.now
        )[0]
        self.assertEqual(item.weight_kg, 55.0)

    def test_state_history_becomes_sorted_measurements(self) -> None:
        payload = json.dumps(
            {
                "mac": self.mac,
                "history": {
                    "weight": [8000, 5500],
                    "utc": [
                        int((self.now + timedelta(minutes=5)).timestamp()),
                        int(self.now.timestamp()),
                    ],
                },
            }
        )
        items = self.parser.parse(f"device/zs7/{self.mac}/state", payload, self.now)
        self.assertEqual([item.weight_kg for item in items], [55.0, 80.0])
        self.assertTrue(all(item.source_kind == "history" for item in items))

    def test_bad_device_time_falls_back_to_received_time(self) -> None:
        payload = json.dumps({"mac": self.mac, "weight": 80, "time": 0})
        item = self.parser.parse(f"device/zs7/{self.mac}/sensor", payload, self.now)[0]
        self.assertEqual(item.measured_at, self.now)
        self.assertEqual(item.timestamp_source, "received")

    def test_mac_mismatch_is_rejected(self) -> None:
        payload = json.dumps({"mac": "aaaaaaaaaaaa", "weight": 80, "time": 0})
        with self.assertRaises(PayloadError):
            self.parser.parse(f"device/zs7/{self.mac}/sensor", payload, self.now)

    def test_invalid_weight_is_rejected(self) -> None:
        payload = json.dumps({"mac": self.mac, "weight": "not-a-number", "time": 0})
        with self.assertRaises(PayloadError):
            self.parser.parse(f"device/zs7/{self.mac}/sensor", payload, self.now)

    def test_invalid_utf8_is_a_payload_error(self) -> None:
        with self.assertRaises(PayloadError):
            self.parser.parse(
                f"device/zs7/{self.mac}/sensor",
                b"\xff\xfe",
                self.now,
            )

    def test_sensor_and_history_share_event_id(self) -> None:
        timestamp = int((self.now - timedelta(seconds=10)).timestamp())
        sensor = self.parser.parse(
            f"device/zs7/{self.mac}/sensor",
            json.dumps({"mac": self.mac, "weight": "80.20", "time": timestamp}),
            self.now,
        )[0]
        history = self.parser.parse(
            f"device/zs7/{self.mac}/state",
            json.dumps(
                {
                    "mac": self.mac,
                    "history": {"weight": [8020], "utc": [timestamp]},
                }
            ),
            self.now,
        )[0]
        self.assertEqual(sensor.event_id, history.event_id)

    def test_equal_unknown_time_history_entries_remain_distinct(self) -> None:
        items = self.parser.parse(
            f"device/zs7/{self.mac}/state",
            json.dumps(
                {
                    "mac": self.mac,
                    "history": {"weight": [8000, 8000], "utc": [0, 0]},
                }
            ),
            self.now,
        )
        self.assertEqual(len(items), 2)
        self.assertNotEqual(items[0].event_id, items[1].event_id)

    def test_unknown_time_history_snapshot_replay_is_idempotent(self) -> None:
        payload = json.dumps(
            {
                "mac": self.mac,
                "history": {"weight": [8000], "utc": [0]},
            }
        )
        first = self.parser.parse(
            f"device/zs7/{self.mac}/state",
            payload,
            self.now,
        )[0]
        second = self.parser.parse(
            f"device/zs7/{self.mac}/state",
            payload,
            self.now + timedelta(hours=1),
        )[0]
        self.assertEqual(first.event_id, second.event_id)


if __name__ == "__main__":
    unittest.main()
