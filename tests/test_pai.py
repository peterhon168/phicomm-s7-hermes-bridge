from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hermes_scale.pai_api import PaiApiClient, PaiAuth
from hermes_scale.service import MeasurementService

from tests.helpers import make_config


class FakeResponse:
    def __init__(self, value: dict[str, object]):
        self.value = value

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.value).encode("utf-8")


class FakeOpener:
    def __init__(self, responses: list[dict[str, object]]):
        self.responses = list(responses)
        self.requests: list[object] = []

    def open(self, request: object, timeout: float) -> FakeResponse:
        self.requests.append(request)
        return FakeResponse(self.responses.pop(0))


class PaiTest(unittest.TestCase):
    def test_signature_is_uppercase_md5_of_sorted_header_material(self) -> None:
        auth = PaiAuth(
            app_id="app",
            app_secret="secret",
            app_version="1.2.3",
            platform="android",
            time_zone="Asia/Shanghai",
            token="token",
            user_id="user",
            member_id=123,
        )
        client = PaiApiClient(auth)
        with patch("hermes_scale.pai_api.time.time", return_value=1_700_000_000.123):
            headers = client._headers()
        self.assertEqual(headers["timestamp"], "1700000000123")
        self.assertEqual(len(headers["sign"]), 32)
        self.assertEqual(headers["sign"], headers["sign"].upper())

    def test_history_and_claim_lists_are_normalized(self) -> None:
        auth = PaiAuth("a", "s", "v", "p", "tz", "t", "u", 7)
        opener = FakeOpener(
            [
                {"code": "0", "data": [{"rawDataId": 9, "weight": 82.1}]},
                {"code": "0", "data": None},
                {
                    "code": "0",
                    "data": {
                        "historyDataBeanList": [
                            {
                                "measureId": 10,
                                "weight": 82.1,
                                "bfr": 38.2,
                                "createTime": 1700000000000,
                            }
                        ]
                    },
                },
            ]
        )
        client = PaiApiClient(auth, opener=opener)
        records = client.sync()
        self.assertEqual(records[0]["measureId"], 10)
        self.assertEqual(len(opener.requests), 3)

    def test_service_records_bodyfat_and_deduplicates_measure_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = MeasurementService(make_config(root))
            try:
                record = {
                    "measureId": 1001,
                    "weight": 82.1,
                    "bfr": 38.2,
                    "createTime": 1785770000000,
                }
                first = service.ingest_pai(record)
                second = service.ingest_pai(record)
                self.assertEqual(first.status, "recorded")
                self.assertEqual(second.status, "duplicate")
                self.assertEqual(first.bodyfat_pct, 38.2)
                raw = (root / "raw").rglob("*.jsonl")
                raw_text = "\n".join(path.read_text(encoding="utf-8") for path in raw)
                self.assertIn('"bodyfat_pct":38.2', raw_text)
            finally:
                service.close()


if __name__ == "__main__":
    unittest.main()
