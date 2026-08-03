from __future__ import annotations

import os
import math
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


PERSON_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class ConfigError(ValueError):
    """Raised when the service configuration is invalid."""


@dataclass(frozen=True)
class MqttConfig:
    host: str
    port: int
    client_id: str
    username: str | None
    password: str | None
    sensor_topic: str
    state_topic: str
    keepalive_seconds: int
    tls: bool


@dataclass(frozen=True)
class StorageConfig:
    root: Path
    timezone: str


@dataclass(frozen=True)
class ProcessingConfig:
    session_window_seconds: int
    outlier_threshold_kg: float
    min_valid_weight_kg: float
    max_valid_weight_kg: float
    weight_divisor: str | float
    ambiguity_margin_kg: float
    recent_baseline_count: int


@dataclass(frozen=True)
class HealthConfig:
    enabled: bool
    host: str
    port: int


@dataclass(frozen=True)
class PaiConfig:
    enabled: bool
    base_url: str
    auth_file: Path
    poll_interval_seconds: int
    auto_claim: bool


@dataclass(frozen=True)
class PersonConfig:
    id: str
    name: str
    min_weight_kg: float
    max_weight_kg: float
    initial_weight_kg: float


@dataclass(frozen=True)
class AppConfig:
    mqtt: MqttConfig
    storage: StorageConfig
    processing: ProcessingConfig
    health: HealthConfig
    pai: PaiConfig
    people: tuple[PersonConfig, ...]


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name)
    if not isinstance(value, dict):
        raise ConfigError(f"missing or invalid [{name}] section")
    return value


def _env(name: str, fallback: Any = None) -> Any:
    value = os.getenv(name)
    return fallback if value is None else value


def _as_bool(value: Any, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise ConfigError(f"{field} must be a boolean")


def _as_int(
    value: Any,
    field: str,
    minimum: int = 1,
    maximum: int | None = None,
) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{field} must be an integer") from exc
    if parsed < minimum:
        raise ConfigError(f"{field} must be >= {minimum}")
    if maximum is not None and parsed > maximum:
        raise ConfigError(f"{field} must be <= {maximum}")
    return parsed


def _as_float(value: Any, field: str, minimum: float | None = None) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{field} must be numeric") from exc
    if not math.isfinite(parsed):
        raise ConfigError(f"{field} must be finite")
    if minimum is not None and parsed < minimum:
        raise ConfigError(f"{field} must be >= {minimum}")
    return parsed


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path).expanduser().resolve()
    try:
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {config_path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {config_path}: {exc}") from exc

    mqtt_data = _section(data, "mqtt")
    storage_data = _section(data, "storage")
    processing_data = _section(data, "processing")
    health_data = _section(data, "health")

    password_env = str(mqtt_data.get("password_env", "HERMES_SCALE_MQTT_PASSWORD"))
    mqtt = MqttConfig(
        host=str(_env("HERMES_SCALE_MQTT_HOST", mqtt_data.get("host", "127.0.0.1"))),
        port=_as_int(
            _env("HERMES_SCALE_MQTT_PORT", mqtt_data.get("port", 1883)),
            "mqtt.port",
            maximum=65535,
        ),
        client_id=str(mqtt_data.get("client_id", "hermes-s7-scale")),
        username=(
            str(_env("HERMES_SCALE_MQTT_USERNAME", mqtt_data.get("username")))
            if _env("HERMES_SCALE_MQTT_USERNAME", mqtt_data.get("username")) is not None
            else None
        ),
        password=os.getenv(password_env),
        sensor_topic=str(mqtt_data.get("sensor_topic", "device/zs7/+/sensor")),
        state_topic=str(mqtt_data.get("state_topic", "device/zs7/+/state")),
        keepalive_seconds=_as_int(
            mqtt_data.get("keepalive_seconds", 30), "mqtt.keepalive_seconds"
        ),
        tls=_as_bool(mqtt_data.get("tls", False), "mqtt.tls"),
    )

    raw_root = Path(
        str(
            _env(
                "HERMES_SCALE_STORAGE_ROOT",
                storage_data.get("root", "./runtime/scale"),
            )
        )
    )
    if not raw_root.is_absolute():
        raw_root = config_path.parent / raw_root
    timezone = str(storage_data.get("timezone", "Asia/Shanghai"))
    try:
        ZoneInfo(timezone)
    except Exception as exc:
        raise ConfigError(f"unknown storage.timezone: {timezone}") from exc
    storage = StorageConfig(root=raw_root.resolve(), timezone=timezone)

    divisor_value = processing_data.get("weight_divisor", "auto")
    if isinstance(divisor_value, str):
        if divisor_value.strip().lower() != "auto":
            raise ConfigError('processing.weight_divisor must be "auto" or a positive number')
        weight_divisor: str | float = "auto"
    else:
        weight_divisor = _as_float(divisor_value, "processing.weight_divisor", 0.000001)

    processing = ProcessingConfig(
        session_window_seconds=_as_int(
            processing_data.get("session_window_seconds", 180),
            "processing.session_window_seconds",
        ),
        outlier_threshold_kg=_as_float(
            processing_data.get("outlier_threshold_kg", 0.5),
            "processing.outlier_threshold_kg",
            0.0,
        ),
        min_valid_weight_kg=_as_float(
            processing_data.get("min_valid_weight_kg", 30.0),
            "processing.min_valid_weight_kg",
            0.0,
        ),
        max_valid_weight_kg=_as_float(
            processing_data.get("max_valid_weight_kg", 200.0),
            "processing.max_valid_weight_kg",
            0.0,
        ),
        weight_divisor=weight_divisor,
        ambiguity_margin_kg=_as_float(
            processing_data.get("ambiguity_margin_kg", 5.0),
            "processing.ambiguity_margin_kg",
            0.0,
        ),
        recent_baseline_count=_as_int(
            processing_data.get("recent_baseline_count", 7),
            "processing.recent_baseline_count",
        ),
    )
    if processing.min_valid_weight_kg >= processing.max_valid_weight_kg:
        raise ConfigError("minimum valid weight must be lower than maximum valid weight")

    health = HealthConfig(
        enabled=_as_bool(health_data.get("enabled", True), "health.enabled"),
        host=str(health_data.get("host", "127.0.0.1")),
        port=_as_int(health_data.get("port", 8080), "health.port", maximum=65535),
    )

    pai_data = data.get("pai", {})
    if not isinstance(pai_data, dict):
        raise ConfigError("[pai] must be a table")
    raw_auth_file = Path(
        str(pai_data.get("auth_file", "/etc/hermes-s7/pai-auth.json"))
    )
    if not raw_auth_file.is_absolute():
        raw_auth_file = config_path.parent / raw_auth_file
    pai = PaiConfig(
        enabled=_as_bool(pai_data.get("enabled", False), "pai.enabled"),
        base_url=str(pai_data.get("base_url", "https://lsprod3.laisitech.com")),
        auth_file=raw_auth_file.resolve(),
        poll_interval_seconds=_as_int(
            pai_data.get("poll_interval_seconds", 60),
            "pai.poll_interval_seconds",
            minimum=15,
            maximum=86400,
        ),
        auto_claim=_as_bool(pai_data.get("auto_claim", True), "pai.auto_claim"),
    )

    people_data = data.get("people")
    if not isinstance(people_data, list) or not people_data:
        raise ConfigError("at least one [[people]] entry is required")
    people: list[PersonConfig] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(people_data):
        if not isinstance(item, dict):
            raise ConfigError(f"people[{index}] must be a table")
        person_id = str(item.get("id", "")).strip()
        if not PERSON_ID_RE.fullmatch(person_id):
            raise ConfigError(f"invalid people[{index}].id: {person_id!r}")
        if person_id in seen_ids:
            raise ConfigError(f"duplicate person id: {person_id}")
        seen_ids.add(person_id)
        minimum = _as_float(item.get("min_weight_kg"), f"people[{index}].min_weight_kg")
        maximum = _as_float(item.get("max_weight_kg"), f"people[{index}].max_weight_kg")
        initial = _as_float(item.get("initial_weight_kg"), f"people[{index}].initial_weight_kg")
        if not minimum <= initial <= maximum:
            raise ConfigError(f"people[{index}] initial weight must be inside its range")
        people.append(
            PersonConfig(
                id=person_id,
                name=str(item.get("name", person_id)),
                min_weight_kg=minimum,
                max_weight_kg=maximum,
                initial_weight_kg=initial,
            )
        )

    return AppConfig(
        mqtt=mqtt,
        storage=storage,
        processing=processing,
        health=health,
        pai=pai,
        people=tuple(people),
    )
