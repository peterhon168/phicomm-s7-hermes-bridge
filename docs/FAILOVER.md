# Long-term independence and failover

The Pai adapter is a convenience source, not the canonical database. Every
accepted measurement is normalized into the project model and stored locally.
That separation is what makes a vendor shutdown survivable.

## What survives a Pai shutdown

Already collected measurements remain in the local SQLite ledger and its
JSONL/CSV/JSON exports. Back those files up independently of the application
and never treat the vendor account as the only copy.

If the app is discontinued but the cloud API still answers, the direct adapter
may continue to work. Its health status must be monitored and its auth material
must stay outside the repository.

If the Pai cloud itself disappears, new measurements cannot be obtained from
that cloud. The project should then switch to one of these sources:

| Fallback | New weight | New body fat | Hardware work | Notes |
|---|---:|---:|---:|---|
| zS7 firmware + local MQTT | Yes | No | Flashing and Wi-Fi setup | Removes the cloud dependency for weight; requires a verified original firmware backup first. |
| ESP8266-to-scale UART gateway | Yes | Maybe | Teardown, 3.3 V UART and protocol capture | The durable route if the scale MCU exposes enough data; body-fat support requires the hand-grip/impedance protocol. |
| Android capture/manual import | Sometimes | Sometimes | App/device maintenance | A transitional recovery path, not a zero-touch design. |
| Replacement local-protocol scale | Yes | Depends on device | New hardware | The cleanest body-fat fallback if the S7 impedance protocol cannot be recovered. |

## Migration procedure

1. Preserve `scale.sqlite3`, the raw JSONL tree and the per-person exports.
2. Keep the Hermes export contract unchanged: adapters only produce normalized
   measurements; storage and agent readers do not depend on the vendor.
3. Add the new adapter behind the same `MeasurementService` interface.
4. Replay a small fixture set and compare assignments, session means and body-fat
   nullability before switching live input.
5. Mark the source in logs and exports so a later chart can distinguish Pai
   estimates from a local impedance measurement.

## Body-fat limitation

Weight alone cannot reconstruct the vendor's body-fat algorithm. Existing `bfr`
values should be retained as estimates with their source metadata. Do not fill
missing body-fat values with a guessed formula and present them as an original
measurement.
