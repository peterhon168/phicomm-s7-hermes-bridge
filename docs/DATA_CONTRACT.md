# Hermes data contract

## Stable entry point

The collector writes `hermes-summary.json` at the root of the configured data
directory. It is atomically replaced after an ingest or session finalization.
It contains:

- `people.<id>.latest`: the person's latest finalized session mean;
- `people.<id>.finalized_session_count`: finalized session count;
- `people.<id>.measurements_csv`: session-level time series path;
- `people.<id>.daily_summary_json`: daily trend path;
- `pending_measurements`: raw measurements requiring review;
- `review_sessions`: sessions without a majority consensus.

Pai body-fat fields are optional. When present, they are consumer estimates and
must retain their source label.

Example (synthetic values):

```json
{
  "schema_version": 1,
  "timezone": "Asia/Shanghai",
  "pending_measurements": 0,
  "people": {
    "person_a": {
      "name": "Person A",
      "finalized_session_count": 12,
      "latest": {
        "ended_at": "2026-01-03T00:02:00+00:00",
        "sample_count": 3,
        "accepted_count": 3,
        "weight_mean_kg": 72.4,
        "bodyfat_mean_pct": 24.1
      },
      "measurements_csv": "users/person_a/measurements.csv",
      "daily_summary_json": "users/person_a/daily-summary.json"
    }
  }
}
```

`latest` is `null` until a person has a finalized session.

## Averaging and state

1. Each stable scale event is a raw sample.
2. Samples for one person and device inside the configured session window are
   grouped into one session.
3. For sessions with at least three samples, values farther than the configured
   outlier threshold from the median are excluded. A strict majority is required
   to finalize; otherwise the session is marked `review`.
4. `weight_mean_kg` is the finalized session mean. Daily summaries average the
   finalized sessions for that local calendar day.

Consumers must not count every raw CSV row as a separate daily measurement or
recompute trends from `raw/`.

Timestamps are ISO-8601. Storage uses UTC timestamps and the configured local
timezone for daily aggregation. `status=open` means the session window is still
active; only `status=finalized` belongs in a trend.

## Source independence and rebuild

`scale.sqlite3` is the canonical ledger; JSONL, CSV and JSON files are derived
exports. If an export is deleted, rebuild it with:

```bash
hermes-scale rebuild-exports --config /etc/hermes-s7/config.toml
```

Adapters should produce normalized measurements and source metadata without
changing this contract.
