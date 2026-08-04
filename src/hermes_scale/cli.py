from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from .config import ConfigError, load_config
from .mqtt_app import MqttRunner
from .parser import PayloadError
from .service import MeasurementService


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ingest Phicomm zS7 weight into Hermes storage")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="run the MQTT consumer")
    run.add_argument("--config", type=Path, required=True)

    ingest = subparsers.add_parser("ingest", help="ingest one MQTT payload without a broker")
    ingest.add_argument("--config", type=Path, required=True)
    ingest.add_argument("--topic", required=True)
    ingest.add_argument("--payload", required=True, help="JSON text, or '-' to read stdin")
    ingest.add_argument(
        "--received-at",
        help="optional ISO-8601 timestamp; defaults to now",
    )

    finalize = subparsers.add_parser("finalize", help="finalize expired sessions")
    finalize.add_argument("--config", type=Path, required=True)
    finalize.add_argument("--at", help="optional ISO-8601 timestamp; defaults to now")

    status = subparsers.add_parser("status", help="show local storage counters")
    status.add_argument("--config", type=Path, required=True)

    rebuild = subparsers.add_parser("rebuild-exports", help="rebuild JSONL/CSV/JSON from SQLite")
    rebuild.add_argument("--config", type=Path, required=True)
    rebuild_sessions = subparsers.add_parser(
        "rebuild-sessions",
        help="rebuild sessions chronologically from the SQLite measurement ledger",
    )
    rebuild_sessions.add_argument("--config", type=Path, required=True)
    rebuild_sessions.add_argument("--at", help="optional ISO-8601 finalization time")
    return parser


def _datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        config = load_config(args.config)
        if args.command == "run":
            MqttRunner(config).run()
            return 0

        with MeasurementService(config) as service:
            if args.command == "ingest":
                payload = sys.stdin.read() if args.payload == "-" else args.payload
                results = service.ingest(
                    args.topic,
                    payload,
                    _datetime(args.received_at),
                )
                print(
                    json.dumps(
                        [result.to_dict() for result in results],
                        ensure_ascii=False,
                        indent=2,
                        sort_keys=True,
                    )
                )
            elif args.command == "finalize":
                count = service.maintenance(_datetime(args.at))
                print(json.dumps({"finalized_sessions": count}, ensure_ascii=False))
            elif args.command == "status":
                print(json.dumps(service.stats(), ensure_ascii=False, indent=2, sort_keys=True))
            elif args.command == "rebuild-exports":
                service.store.rebuild_exports()
                print(json.dumps({"status": "rebuilt"}, ensure_ascii=False))
            elif args.command == "rebuild-sessions":
                count = service.rebuild_sessions(_datetime(args.at))
                print(
                    json.dumps(
                        {"status": "rebuilt", "sessions": count},
                        ensure_ascii=False,
                    )
                )
        return 0
    except (ConfigError, PayloadError, ValueError, OSError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
