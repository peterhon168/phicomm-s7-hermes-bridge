# HermesAgent integration

## One-time registration

Register the configured data root's `hermes-summary.json` as a read-only health
source in the health profile. Do not copy the JSON into the profile: keeping one
canonical file prevents stale duplicates.

The registration should state:

- the root summary path and its `people.<id>` namespaces;
- that only finalized sessions and daily summaries belong in trends;
- that `pending_measurements` and `review_sessions` are excluded by default;
- that Pai body-fat values are consumer estimates;
- that a second person's records must not be written into a single-person diary.

## Relationship to an existing health diary

An existing single-person diary can remain a separate source for older history,
food and medication records. If a legacy chart only understands that diary, a
one-way projection may include `person_a` measurements with an explicit
`source=pai-s7` marker. Never overwrite history and never project `person_b`
into the single-person diary.

## Chart data

The public chart workflow keeps the historical baseline from the legacy diary
and merges finalized S7 daily means for `person_a`. If a date exists in both,
the S7 daily mean is used once. `person_b` is kept separate unless a separate
chart is explicitly requested.

## Operational behavior

The collector polls the source in the background; an agent does not need to
open the vendor app or manually copy a reading after every measurement. The
agent reads the stable summary when a health query or chart request is made.
