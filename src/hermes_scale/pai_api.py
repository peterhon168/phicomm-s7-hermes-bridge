from __future__ import annotations

import hashlib
import json
import logging
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


LOGGER = logging.getLogger(__name__)


_EARLIEST_PAI_TIME = datetime(2017, 1, 1, tzinfo=UTC)


def _pai_record_sort_key(record: dict[str, Any]) -> tuple[datetime, str]:
    """Return a chronological key without changing the source record.

    Pai's history endpoint has returned rows out of order in practice.  The
    storage layer groups sessions chronologically, so valid device timestamps
    must be ordered before they are handed to the poller.  Rows without a
    usable timestamp remain stable and are processed last.
    """

    raw_time = record.get("createTime", record.get("timestamp"))
    candidate: datetime | None = None
    if raw_time is not None and str(raw_time).strip():
        text = str(raw_time).strip()
        try:
            numeric = Decimal(text)
            if numeric.is_finite():
                seconds = (
                    float(numeric / Decimal(1000))
                    if abs(numeric) > 100_000_000_000
                    else float(numeric)
                )
                candidate = datetime.fromtimestamp(seconds, tz=UTC)
        except (InvalidOperation, TypeError, ValueError, OSError, OverflowError):
            try:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
                if parsed.tzinfo is not None:
                    candidate = parsed.astimezone(UTC)
            except ValueError:
                candidate = None

    now = datetime.now(UTC)
    if candidate is None or candidate < _EARLIEST_PAI_TIME or candidate > now + timedelta(days=7):
        candidate = datetime.max.replace(tzinfo=UTC)
    tie_breaker = str(record.get("measureId", record.get("rawDataId", "")))
    return candidate, tie_breaker


class PaiApiError(RuntimeError):
    """The Pai Health API could not return a usable response."""


class PaiAuthExpired(PaiApiError):
    """The access token or signature material must be refreshed."""


@dataclass(frozen=True)
class PaiAuth:
    app_id: str
    app_secret: str
    app_version: str
    platform: str
    time_zone: str
    token: str
    user_id: str
    member_id: int

    @classmethod
    def load(cls, path: str | Path) -> "PaiAuth":
        auth_path = Path(path).expanduser()
        try:
            value = json.loads(auth_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise PaiApiError(f"Pai auth file not found: {auth_path}") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise PaiApiError(f"Pai auth file is unreadable: {auth_path}") from exc
        if not isinstance(value, dict):
            raise PaiApiError("Pai auth file must contain a JSON object")

        def required(name: str) -> str:
            item = value.get(name)
            if not isinstance(item, str) or not item.strip():
                raise PaiApiError(f"Pai auth field is missing: {name}")
            return item.strip()

        try:
            member_id = int(str(value.get("memberId", "")).strip())
        except (TypeError, ValueError) as exc:
            raise PaiApiError("Pai auth field is invalid: memberId") from exc
        if member_id <= 0:
            raise PaiApiError("Pai auth field is invalid: memberId")
        return cls(
            app_id=required("appId"),
            app_secret=required("appSecret"),
            app_version=required("appVersion"),
            platform=required("platform"),
            time_zone=required("timeZone"),
            token=required("token"),
            user_id=required("userId"),
            member_id=member_id,
        )


class PaiApiClient:
    """Small dependency-free client for the verified Pai Health JSON routes."""

    def __init__(
        self,
        auth: PaiAuth,
        *,
        base_url: str = "https://lsprod3.laisitech.com",
        timeout_seconds: float = 20.0,
        opener: urllib.request.OpenerDirector | None = None,
    ) -> None:
        self.auth = auth
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._opener = opener or urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=ssl.create_default_context())
        )

    def _headers(self) -> dict[str, str]:
        timestamp = str(int(time.time() * 1000))
        canonical = {
            "appId": self.auth.app_id,
            "appVersion": self.auth.app_version,
            "platform": self.auth.platform,
            "timeZone": self.auth.time_zone,
            "timestamp": timestamp,
            "token": self.auth.token,
            "userId": self.auth.user_id,
            "version": "v1",
        }
        material = "&".join(
            f"{key}={canonical[key]}" for key in sorted(canonical)
        )
        material += f"&APP_SECRET={self.auth.app_secret}"
        signed = hashlib.md5(material.encode("utf-8")).hexdigest().upper()
        return {
            "appid": self.auth.app_id,
            "appversion": self.auth.app_version,
            "platform": self.auth.platform,
            "timezone": self.auth.time_zone,
            "timestamp": timestamp,
            "token": self.auth.token,
            "userid": self.auth.user_id,
            "version": "v1",
            "sign": signed,
            "content-type": "application/json; charset=utf-8",
        }

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self.base_url}/{path.lstrip('/')}",
            data=json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            ),
            headers=self._headers(),
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=self.timeout_seconds) as response:
                raw = response.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise PaiApiError(f"Pai request failed: {path}: {exc}") from exc
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PaiApiError(f"Pai response is not JSON: {path}") from exc
        if not isinstance(value, dict):
            raise PaiApiError(f"Pai response is not an object: {path}")
        code = str(value.get("code", ""))
        if code in {"2000", "2001"}:
            raise PaiAuthExpired(f"Pai authentication rejected ({code}) on {path}")
        if code not in {"", "0", "200"}:
            raise PaiApiError(f"Pai API returned code {code} on {path}")
        return value

    @staticmethod
    def _list(value: Any) -> list[dict[str, Any]]:
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            # Some Pai endpoints serialize a list directly as data, while
            # history nests it under historyDataBeanList.
            for key in ("historyDataBeanList", "list", "items"):
                candidate = value.get(key)
                if isinstance(candidate, list):
                    return [item for item in candidate if isinstance(item, dict)]
        return []

    def claim_pending(self) -> int:
        response = self._post(
            "/balance/claim/data/get",
            {"memberId": self.auth.member_id},
        )
        pending = self._list(response.get("data"))
        claimed = 0
        for item in pending:
            raw_id = item.get("rawDataId")
            try:
                raw_data_id = int(str(raw_id))
            except (TypeError, ValueError):
                LOGGER.warning("Ignoring Pai claim row without rawDataId")
                continue
            self._post(
                "/balance/claim/data/own",
                {"memberId": self.auth.member_id, "rawDataId": raw_data_id},
            )
            claimed += 1
        return claimed

    def history(self) -> list[dict[str, Any]]:
        # The verified endpoint returns the most recent rows when the cursor is
        # ahead of the current cloud time.  Deduplication is done by measureId
        # in the SQLite event ledger, so fetching the small recent window on
        # every poll is safe and survives a bridge restart.
        cursor = int(time.time() * 1000) + 86_400_000
        response = self._post(
            "/balance/history/data/get",
            {"currentLatestTimestamp": cursor, "memberId": self.auth.member_id},
        )
        return self._list(response.get("data"))

    def recent_home(self) -> list[dict[str, Any]]:
        response = self._post(
            "/newHomePage/v2/card/list",
            {
                "timeStamp": "0",
                "deviceTypes": [],
                "userId": self.auth.user_id,
                "memberId": str(self.auth.member_id),
            },
        )
        records: list[dict[str, Any]] = []
        for card in self._list(response.get("data")):
            card_data = card.get("data")
            if not isinstance(card_data, dict):
                continue
            recent = card_data.get("recentData")
            records.extend(self._list(recent))
            if not recent and "weight" in card_data:
                records.append(card_data)
        return records

    def sync(self, *, auto_claim: bool = True) -> list[dict[str, Any]]:
        if auto_claim:
            claimed = self.claim_pending()
            if claimed:
                LOGGER.info("Pai claimed %d pending row(s)", claimed)
        records = self.history()
        if not records:
            records = self.recent_home()
        return sorted(records, key=_pai_record_sort_key)
