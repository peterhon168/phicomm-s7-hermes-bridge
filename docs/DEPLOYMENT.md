# Deploying to a Hermes host

This document describes a generic deployment. Replace every placeholder with
values for the operator's own host; do not commit the resulting files.

## 1. Prepare configuration

The host needs Docker Engine. Copy the templates and keep secrets local:

```bash
cd deploy
cp .env.example .env
cp config.example.toml config.toml
chmod 600 .env
chmod 644 config.toml
```

Set a long random `MQTT_PASSWORD`, a LAN-only `MQTT_BIND_ADDRESS`, a writable
`HERMES_SCALE_DIR`, and matching `HERMES_SCALE_UID/GID`. Create the directory
with the configured owner before starting the container.

If the Pai adapter is enabled, store its JSON auth file outside Git with mode
`0600` and set `PAI_AUTH_FILE`. The installer mounts it read-only at
`/etc/hermes-s7/pai-auth.json`.

## 2. Start the services

For a host with Compose v2:

```bash
docker compose --env-file .env config --quiet
docker compose --env-file .env up -d --build --wait --wait-timeout 120
docker compose --env-file .env ps
curl -fsS http://127.0.0.1:18087/health
```

The native Docker installer in `install-with-docker.sh` is useful on hosts
without Compose. It replaces only containers carrying the project's managed
label and uses `restart: unless-stopped` semantics.

## 3. zS7/MQTT topics

Configure the scale or zS7 firmware to publish to the host's LAN address on
port 1883 with the credentials from `.env`:

```text
device/zs7/<device-id>/sensor
device/zs7/<device-id>/state
```

The collector subscribes to both and deduplicates replayed history. Keep the
broker on a trusted LAN; do not expose it to the Internet.

## 4. Storage and backups

The configured data directory contains:

```text
scale.sqlite3
hermes-summary.json
raw/YYYY/MM/YYYY-MM-DD.jsonl
pending/measurements.jsonl
users/<person-id>/measurements.csv
users/<person-id>/daily-summary.json
```

SQLite is the source of truth. Back up the database together with its `-wal` and
`-shm` files, or stop the collector for a consistent copy. Keep backups on a
separate medium and never put them in a public repository.

## 5. Health and recovery

The health endpoint is intended for localhost monitoring. A transient vendor
API failure marks the Pai status degraded while the collector continues retrying;
an exited container is restarted by Docker. If the vendor disappears permanently,
follow [FAILOVER.md](FAILOVER.md) and switch to a local source adapter.
