from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from hermes_scale.mqtt_app import MqttRunner

from tests.helpers import make_config


class FakeMqttClient:
    last: "FakeMqttClient | None" = None

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        type(self).last = self
        self.options = _kwargs
        self.on_connect = None
        self.on_disconnect = None
        self.on_message = None
        self.subscriptions: list[tuple[str, int]] = []
        self.connection: tuple[str, int, int] | None = None
        self.auth: tuple[str, str | None] | None = None
        self.acks: list[tuple[int, int]] = []
        self.published: list[tuple[str, str, int]] = []

    def username_pw_set(self, username: str, password: str | None) -> None:
        self.auth = (username, password)

    def tls_set(self) -> None:
        raise AssertionError("TLS was not configured for this test")

    def reconnect_delay_set(self, **_kwargs: int) -> None:
        return None

    def subscribe(self, topic: str, qos: int) -> None:
        self.subscriptions.append((topic, qos))

    def ack(self, mid: int, qos: int) -> int:
        self.acks.append((mid, qos))
        return 0

    def publish(self, topic: str, payload: str, qos: int) -> SimpleNamespace:
        self.published.append((topic, payload, qos))
        return SimpleNamespace(rc=0)

    def connect(self, host: str, port: int, keepalive: int) -> None:
        self.connection = (host, port, keepalive)

    def loop_forever(self, *, retry_first_connection: bool) -> None:
        if not retry_first_connection:
            raise AssertionError("first connection should be retried")
        assert self.on_connect is not None
        assert self.on_message is not None
        self.on_connect(self, None, None, 0, None)
        now = datetime.now(UTC)
        payload = json.dumps(
            {
                "mac": "1234567890ab",
                "weight": "80.20",
                "time": str(int(now.timestamp())),
            }
        ).encode("utf-8")
        self.on_message(
            self,
            None,
            SimpleNamespace(
                topic="device/zs7/1234567890ab/sensor",
                payload=payload,
                qos=1,
                mid=42,
            ),
        )

    def disconnect(self) -> None:
        return None


class MqttRunnerTest(unittest.TestCase):
    def test_subscribes_and_ingests_a_message(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = make_config(root)
            with patch("paho.mqtt.client.Client", FakeMqttClient):
                MqttRunner(config).run()

            client = FakeMqttClient.last
            self.assertIsNotNone(client)
            assert client is not None
            self.assertEqual(client.connection, ("127.0.0.1", 1883, 30))
            self.assertFalse(client.options["clean_session"])
            self.assertTrue(client.options["manual_ack"])
            self.assertEqual(client.acks, [(42, 1)])
            self.assertEqual(
                client.subscriptions,
                [
                    ("device/zs7/+/sensor", 1),
                    ("device/zs7/+/state", 1),
                ],
            )
            with sqlite3.connect(root / "scale.sqlite3") as connection:
                count = connection.execute(
                    "SELECT COUNT(*) FROM raw_measurements"
                ).fetchone()[0]
            self.assertEqual(count, 1)

    def test_refuses_configured_username_without_password(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = make_config(Path(directory))
            mqtt = replace(config.mqtt, username="s7", password=None)
            with self.assertRaises(RuntimeError):
                MqttRunner(replace(config, mqtt=mqtt))

    def test_requests_history_for_a_known_device_after_connect(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = make_config(Path(directory))
            runner = MqttRunner(config)
            now = datetime.now(UTC)
            runner.service.ingest(
                "device/zs7/aaaaaaaaaaaa/sensor",
                json.dumps(
                    {
                        "mac": "aaaaaaaaaaaa",
                        "weight": "55.00",
                        "time": str(int(now.timestamp())),
                    }
                ),
                now,
            )
            with patch("paho.mqtt.client.Client", FakeMqttClient):
                runner.run()

            client = FakeMqttClient.last
            assert client is not None
            self.assertEqual(
                client.published,
                [
                    (
                        "device/zs7/aaaaaaaaaaaa/set",
                        '{"mac":"aaaaaaaaaaaa","history":null}',
                        1,
                    )
                ],
            )

    def test_storage_failure_is_not_acknowledged_and_escapes_loop(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runner = MqttRunner(make_config(Path(directory)))
            with (
                patch("paho.mqtt.client.Client", FakeMqttClient),
                patch.object(runner.service, "ingest", side_effect=OSError("disk full")),
                self.assertRaisesRegex(OSError, "disk full"),
            ):
                runner.run()

            client = FakeMqttClient.last
            assert client is not None
            self.assertEqual(client.acks, [])
            self.assertEqual(runner.status.last_error, "disk full")

    def test_operational_error_degrades_health(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runner = MqttRunner(make_config(Path(directory)))
            runner.status.connected(True)
            self.assertEqual(runner.status.snapshot(runner.service)["status"], "ok")
            runner.status.error("storage unavailable")
            self.assertEqual(runner.status.snapshot(runner.service)["status"], "degraded")
            runner.service.close()


if __name__ == "__main__":
    unittest.main()
