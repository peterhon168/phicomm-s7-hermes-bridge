from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hermes_scale.config import ConfigError, load_config


CONFIG = """
[mqtt]
host = "mqtt.local"
port = 1883
username = "s7"
password_env = "TEST_MQTT_PASSWORD"

[storage]
root = "./data"
timezone = "Asia/Shanghai"

[processing]
session_window_seconds = 180
outlier_threshold_kg = 0.5
min_valid_weight_kg = 30
max_valid_weight_kg = 200
weight_divisor = "auto"
ambiguity_margin_kg = 5
recent_baseline_count = 7

[health]
enabled = false
host = "127.0.0.1"
port = 8080

[[people]]
id = "person_a"
name = "Person A"
min_weight_kg = 70
max_weight_kg = 95
initial_weight_kg = 80
"""


class ConfigTest(unittest.TestCase):
    def test_loads_relative_storage_and_password_from_env(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(CONFIG, encoding="utf-8")
            with patch.dict(os.environ, {"TEST_MQTT_PASSWORD": "secret"}, clear=False):
                config = load_config(path)
            self.assertEqual(config.storage.root, (Path(directory) / "data").resolve())
            self.assertEqual(config.mqtt.password, "secret")

    def test_rejects_person_with_initial_weight_outside_range(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(
                CONFIG.replace("initial_weight_kg = 80", "initial_weight_kg = 60"),
                encoding="utf-8",
            )
            with self.assertRaises(ConfigError):
                load_config(path)

    def test_rejects_non_finite_processing_number(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(
                CONFIG.replace("outlier_threshold_kg = 0.5", "outlier_threshold_kg = nan"),
                encoding="utf-8",
            )
            with self.assertRaises(ConfigError):
                load_config(path)

    def test_rejects_port_above_tcp_range(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(
                CONFIG.replace("port = 1883", "port = 70000", 1),
                encoding="utf-8",
            )
            with self.assertRaises(ConfigError):
                load_config(path)

    def test_health_host_defaults_to_loopback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            path.write_text(
                CONFIG.replace('host = "127.0.0.1"\nport = 8080', "port = 8080"),
                encoding="utf-8",
            )
            config = load_config(path)
            self.assertEqual(config.health.host, "127.0.0.1")


if __name__ == "__main__":
    unittest.main()
