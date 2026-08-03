from __future__ import annotations

import json
import logging
import threading
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .config import AppConfig
from .pai_app import PaiPoller
from .parser import PayloadError
from .service import MeasurementService


LOGGER = logging.getLogger(__name__)


class RuntimeStatus:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.mqtt_connected = False
        self.last_message_at: str | None = None
        self.last_error: str | None = None
        self.last_rejected_at: str | None = None
        self.last_rejected_error: str | None = None
        self.pai_enabled_flag = False
        self.pai_last_sync_at: str | None = None
        self.pai_last_error: str | None = None
        self.pai_last_record_count = 0
        self.pai_last_recorded_count = 0

    def connected(self, value: bool) -> None:
        with self._lock:
            self.mqtt_connected = value

    def message_received(self) -> None:
        with self._lock:
            self.last_message_at = datetime.now(UTC).isoformat(timespec="seconds")
            self.last_error = None

    def error(self, message: str) -> None:
        with self._lock:
            self.last_error = message

    def rejected(self, message: str) -> None:
        with self._lock:
            self.last_rejected_at = datetime.now(UTC).isoformat(timespec="seconds")
            self.last_rejected_error = message

    def pai_enabled(self, value: bool) -> None:
        with self._lock:
            self.pai_enabled_flag = value

    def pai_synced(self, received_count: int, recorded_count: int) -> None:
        with self._lock:
            self.pai_enabled_flag = True
            self.pai_last_sync_at = datetime.now(UTC).isoformat(timespec="seconds")
            self.pai_last_error = None
            self.pai_last_record_count = received_count
            self.pai_last_recorded_count = recorded_count

    def pai_failed(self, message: str) -> None:
        with self._lock:
            self.pai_enabled_flag = True
            self.pai_last_error = message

    def snapshot(self, service: MeasurementService) -> dict[str, Any]:
        with self._lock:
            runtime = {
                "mqtt_connected": self.mqtt_connected,
                "last_message_at": self.last_message_at,
                "last_error": self.last_error,
                "last_rejected_at": self.last_rejected_at,
                "last_rejected_error": self.last_rejected_error,
                "pai_enabled": self.pai_enabled_flag,
                "pai_last_sync_at": self.pai_last_sync_at,
                "pai_last_error": self.pai_last_error,
                "pai_last_record_count": self.pai_last_record_count,
                "pai_last_recorded_count": self.pai_last_recorded_count,
            }
        return {
            "status": (
                "ok"
                if runtime["mqtt_connected"]
                and runtime["last_error"] is None
                and (not runtime["pai_enabled"] or runtime["pai_last_error"] is None)
                else "degraded"
            ),
            **runtime,
            "storage": service.stats(),
        }


class HealthServer:
    def __init__(
        self,
        host: str,
        port: int,
        status: RuntimeStatus,
        service: MeasurementService,
    ) -> None:
        status_ref = status
        service_ref = service

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
                if self.path not in {"/health", "/healthz"}:
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                body = status_ref.snapshot(service_ref)
                encoded = (json.dumps(body, ensure_ascii=False, sort_keys=True) + "\n").encode(
                    "utf-8"
                )
                code = HTTPStatus.OK if body["status"] == "ok" else HTTPStatus.SERVICE_UNAVAILABLE
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, format: str, *args: object) -> None:
                LOGGER.debug("health: " + format, *args)

        self._server = ThreadingHTTPServer((host, port), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="health-server",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


class MqttRunner:
    def __init__(self, config: AppConfig):
        if not config.mqtt.client_id.strip():
            raise RuntimeError("a non-empty MQTT client id is required for a persistent session")
        if config.mqtt.username is not None and config.mqtt.password is None:
            raise RuntimeError(
                "MQTT username is configured but its password environment variable is missing"
            )
        self.config = config
        self.service = MeasurementService(config)
        self.status = RuntimeStatus()
        self._stop = threading.Event()
        self._health: HealthServer | None = None
        self._maintenance_thread: threading.Thread | None = None
        self._pai_poller: PaiPoller | None = None
        self._pai_thread: threading.Thread | None = None

    def run(self) -> None:
        try:
            import paho.mqtt.client as mqtt
        except ImportError as exc:
            raise RuntimeError("paho-mqtt is required for the run command") from exc

        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=self.config.mqtt.client_id,
            clean_session=False,
            protocol=mqtt.MQTTv311,
            manual_ack=True,
        )
        if self.config.mqtt.username is not None:
            client.username_pw_set(
                self.config.mqtt.username,
                self.config.mqtt.password,
            )
        if self.config.mqtt.tls:
            client.tls_set()
        client.reconnect_delay_set(min_delay=1, max_delay=30)

        def on_connect(
            mqtt_client: Any,
            _userdata: Any,
            _flags: Any,
            reason_code: Any,
            _properties: Any,
        ) -> None:
            if reason_code != 0:
                message = f"MQTT connection rejected: {reason_code}"
                self.status.error(message)
                LOGGER.error(message)
                return
            self.status.connected(True)
            mqtt_client.subscribe(self.config.mqtt.sensor_topic, qos=1)
            mqtt_client.subscribe(self.config.mqtt.state_topic, qos=1)
            for device_id in self.service.store.known_device_ids():
                request = json.dumps(
                    {"mac": device_id, "history": None},
                    separators=(",", ":"),
                )
                info = mqtt_client.publish(
                    f"device/zs7/{device_id}/set",
                    request,
                    qos=1,
                )
                if info.rc != mqtt.MQTT_ERR_SUCCESS:
                    raise RuntimeError(
                        f"failed to request zS7 history for {device_id}: {info.rc}"
                    )
            LOGGER.info(
                "MQTT connected; subscribed to %s and %s",
                self.config.mqtt.sensor_topic,
                self.config.mqtt.state_topic,
            )

        def on_disconnect(
            _mqtt_client: Any,
            _userdata: Any,
            _disconnect_flags: Any,
            reason_code: Any,
            _properties: Any,
        ) -> None:
            self.status.connected(False)
            if reason_code != 0:
                LOGGER.warning("unexpected MQTT disconnect: %s", reason_code)

        def on_message(_mqtt_client: Any, _userdata: Any, message: Any) -> None:
            def acknowledge() -> None:
                if message.qos > 0:
                    result = _mqtt_client.ack(message.mid, message.qos)
                    if result != mqtt.MQTT_ERR_SUCCESS:
                        raise RuntimeError(f"failed to acknowledge MQTT message: {result}")

            try:
                results = self.service.ingest(message.topic, message.payload)
                acknowledge()
                self.status.message_received()
                for result in results:
                    LOGGER.info(
                        "measurement %s: %.3f kg person=%s status=%s",
                        result.event_id[:12],
                        result.weight_kg,
                        result.person_id or "pending",
                        result.status,
                    )
            except PayloadError as exc:
                # A permanently invalid payload must be acknowledged so it cannot
                # poison a persistent MQTT session forever.
                acknowledge()
                self.status.rejected(str(exc))
                LOGGER.warning("rejected MQTT payload on %s: %s", message.topic, exc)
            except Exception as exc:
                self.status.error(str(exc))
                LOGGER.exception("failed to ingest MQTT payload on %s", message.topic)
                # With manual acknowledgements and a persistent session, letting
                # the loop fail preserves an unacked QoS1 message for redelivery.
                raise

        client.on_connect = on_connect
        client.on_disconnect = on_disconnect
        client.on_message = on_message

        if self.config.health.enabled:
            self._health = HealthServer(
                self.config.health.host,
                self.config.health.port,
                self.status,
                self.service,
            )
            self._health.start()
        self._maintenance_thread = threading.Thread(
            target=self._maintenance_loop,
            name="session-maintenance",
            daemon=True,
        )
        self._maintenance_thread.start()
        if self.config.pai.enabled:
            self._pai_poller = PaiPoller(self.config.pai, self.service, self.status)
            self._pai_thread = threading.Thread(
                target=self._pai_poller.run,
                name="pai-poller",
                daemon=True,
            )
            self._pai_thread.start()

        try:
            client.connect(
                self.config.mqtt.host,
                self.config.mqtt.port,
                self.config.mqtt.keepalive_seconds,
            )
            client.loop_forever(retry_first_connection=True)
        finally:
            self._stop.set()
            if self._pai_poller is not None:
                self._pai_poller.stop()
            self.status.connected(False)
            try:
                client.disconnect()
            except Exception:
                LOGGER.debug("MQTT disconnect during shutdown failed", exc_info=True)
            if self._maintenance_thread is not None:
                self._maintenance_thread.join(timeout=5)
            if self._pai_thread is not None:
                self._pai_thread.join(timeout=5)
            if self._health is not None:
                self._health.close()
            self.service.close()

    def _maintenance_loop(self) -> None:
        while not self._stop.wait(timeout=30):
            try:
                finalized = self.service.maintenance()
                if finalized:
                    LOGGER.info("finalized %d expired measurement session(s)", finalized)
            except Exception as exc:
                self.status.error(str(exc))
                LOGGER.exception("session maintenance failed")
