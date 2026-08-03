# Phicomm S7 → Hermes bridge

Self-hosted ingestion for Phicomm S7 measurements. The project keeps the
source adapter replaceable and writes a stable local data contract for Hermes:

- Pai Health cloud polling with automatic claim, weight/body-fat parsing and
  idempotent replay;
- zS7/MQTT ingestion as a self-hosted weight-only fallback;
- configurable multi-person assignment, short-session averaging and outlier
  handling;
- SQLite as the source of truth, with JSONL, per-person CSV and daily exports;
- a privacy-preserving `hermes-summary.json` entry point for an agent or other
  local consumer.

The Pai body-fat value is a consumer estimate. Without the hand-held
electrodes it must not be described as a complete eight-electrode measurement.

## Architecture

```text
S7 → Pai cloud/API adapter ─┐
                            ├→ canonical Measurement → SQLite → exports
S7/zS7 → MQTT adapter ─────┘
```

The internal measurement model is independent of Pai. If the vendor service
disappears, historical data remains local and the MQTT/UART fallback can be
used without changing the Hermes export contract.

## Quick start

Requirements: Python 3.11+, an MQTT broker for the zS7 path, and a writable
storage directory.

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
cp config.example.toml config.toml
.venv/bin/hermes-scale ingest \
  --config config.toml \
  --topic device/zs7/example/sensor \
  --payload '{"mac":"example","weight":"72.40","time":"1785686400"}'
```

Run the test suite:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Deployment templates are in [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md). The
source contract is documented in [docs/DATA_CONTRACT.md](docs/DATA_CONTRACT.md)
and the long-term fallback plan in [docs/FAILOVER.md](docs/FAILOVER.md).

## Privacy and credentials

Never commit Pai authentication material, MQTT passwords, private keys,
captures, firmware images, SQLite files or personal health exports. Use local
ignored files and environment variables; see [SECURITY.md](SECURITY.md).

## Status

The adapters, storage layer, two-person assignment, body-fat fields and tests
are implemented. The Pai adapter is optional and disabled by default in the
example configuration. zS7/MQTT remains the local weight-only fallback.
