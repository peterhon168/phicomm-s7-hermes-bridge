# Project roadmap

## Goal

Collect Phicomm S7 measurements without daily manual app operation, assign them
to configured people, average stable sessions, and expose a durable local data
contract to Hermes.

## Phase 0 — public protocol and safety review

- Confirm zS7 MQTT/UDP topics and limitations.
- Keep device firmware backups outside the repository.
- Separate source adapters from storage and agent exports.

## Phase 1 — automatic data bridge

### 1A. Canonical software loop — complete

- MQTT and Pai adapters produce the same normalized measurement model.
- SQLite provides an idempotent raw ledger.
- Sessions, outlier handling, person assignment and pending review are derived
  deterministically.
- JSONL, CSV, daily summaries and `hermes-summary.json` are exported.

### 1B. Pai cloud adapter — optional

- Poll pending and history endpoints with credentials stored outside Git.
- Preserve source IDs, body-fat fields and estimate quality.
- Retry transient failures and expose auth/API errors through health status.

The adapter is intentionally optional. The project must remain useful when the
vendor app, account or API changes.

### 1C. Self-hosted weight fallback — complete in software

The zS7/MQTT path can continue weight collection without the vendor cloud. It
requires a verified firmware path and local network setup on the physical scale.

## Phase 2 — hardware protocol investigation

Only after making two independent original-firmware backups:

1. capture the scale MCU-to-network-controller UART;
2. compare empty, weight-only and hand-grip measurements;
3. document frame boundaries, checksums and impedance fields;
4. add fixtures before changing hardware or firmware.

No body-fat algorithm should be claimed without a repeatable physical signal.

## Phase 3 — independent local firmware

If the UART protocol is stable, keep the scale's measurement MCU and replace
only the networking layer with a local MQTT client, offline queue and OTA-safe
update path. The output must remain compatible with the Phase 1 contract.

## Phase 4 — long-term operation

- daily database/export backups;
- health monitoring and auth-expiry alerts;
- source migration tools and replay fixtures;
- explicit labels distinguishing Pai estimates from local impedance results.

See [FAILOVER.md](FAILOVER.md) for the vendor-shutdown procedure.
