from __future__ import annotations

from pathlib import Path

from hermes_scale.config import (
    AppConfig,
    HealthConfig,
    MqttConfig,
    PaiConfig,
    PersonConfig,
    ProcessingConfig,
    StorageConfig,
)


def make_config(
    root: Path,
    *,
    weight_divisor: str | float = "auto",
    people: tuple[PersonConfig, ...] | None = None,
) -> AppConfig:
    return AppConfig(
        mqtt=MqttConfig(
            host="127.0.0.1",
            port=1883,
            client_id="test-hermes-scale",
            username=None,
            password=None,
            sensor_topic="device/zs7/+/sensor",
            state_topic="device/zs7/+/state",
            keepalive_seconds=30,
            tls=False,
        ),
        storage=StorageConfig(root=root, timezone="Asia/Shanghai"),
        processing=ProcessingConfig(
            session_window_seconds=180,
            outlier_threshold_kg=0.5,
            min_valid_weight_kg=30.0,
            max_valid_weight_kg=200.0,
            weight_divisor=weight_divisor,
            ambiguity_margin_kg=5.0,
            recent_baseline_count=7,
        ),
        health=HealthConfig(enabled=False, host="127.0.0.1", port=8080),
        pai=PaiConfig(
            enabled=False,
            base_url="https://lsprod3.laisitech.com",
            auth_file=root / "pai-auth.json",
            poll_interval_seconds=60,
            auto_claim=False,
        ),
        people=people
        or (
            PersonConfig("person_a", "Person A", 70.0, 95.0, 80.0),
            PersonConfig("person_b", "Person B", 40.0, 65.0, 55.0),
        ),
    )
