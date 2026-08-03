from __future__ import annotations

import logging
import threading
from typing import Any

from .config import PaiConfig
from .pai_api import PaiApiClient, PaiApiError, PaiAuth
from .service import MeasurementService


LOGGER = logging.getLogger(__name__)


class PaiPoller:
    """Poll Pai's recent history and feed rows into the canonical service."""

    def __init__(self, config: PaiConfig, service: MeasurementService, status: Any):
        self.config = config
        self.service = service
        self.status = status
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def sync_once(self) -> int:
        auth = PaiAuth.load(self.config.auth_file)
        client = PaiApiClient(auth, base_url=self.config.base_url)
        records = client.sync(auto_claim=self.config.auto_claim)
        recorded = 0
        for record in records:
            result = self.service.ingest_pai(record)
            if result.status == "recorded":
                recorded += 1
            LOGGER.info(
                "Pai measurement %s: %.3f kg bodyfat=%s person=%s status=%s",
                result.event_id[:12],
                result.weight_kg,
                f"{result.bodyfat_pct:.3f}%" if result.bodyfat_pct is not None else "n/a",
                result.person_id or "pending",
                result.status,
            )
        self.status.pai_synced(len(records), recorded)
        return recorded

    def run(self) -> None:
        self.status.pai_enabled(True)
        while not self._stop.is_set():
            try:
                self.sync_once()
            except PaiApiError as exc:
                self.status.pai_failed(str(exc))
                LOGGER.warning("Pai sync failed: %s", exc)
            except Exception as exc:  # pragma: no cover - defensive process guard
                self.status.pai_failed(str(exc))
                LOGGER.exception("Unexpected Pai sync failure")
            self._stop.wait(self.config.poll_interval_seconds)
