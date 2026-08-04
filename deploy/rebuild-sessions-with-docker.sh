#!/usr/bin/env bash
set -euo pipefail

if [[ $(id -u) -ne 0 ]]; then
  echo "error: run this maintenance command through sudo" >&2
  exit 2
fi

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
env_file="$script_dir/.env"
config_file="$script_dir/config.toml"

if [[ ! -r "$env_file" || ! -r "$config_file" ]]; then
  echo "error: deploy/.env and deploy/config.toml are required" >&2
  exit 2
fi

set -a
# shellcheck disable=SC1090
source "$env_file"
set +a

: "${HERMES_SCALE_DIR:?missing HERMES_SCALE_DIR}"
: "${HERMES_SCALE_UID:?missing HERMES_SCALE_UID}"
: "${HERMES_SCALE_GID:?missing HERMES_SCALE_GID}"

collector_container=hermes-s7-collector
collector_image=hermes-s7-collector:0.1.0
rebuild_container=hermes-s7-session-rebuild

if ! docker image inspect "$collector_image" >/dev/null 2>&1; then
  echo "error: collector image is missing; run deploy/install-with-docker.sh first" >&2
  exit 2
fi
if [[ ! -d "$HERMES_SCALE_DIR" ]]; then
  echo "error: data directory does not exist: $HERMES_SCALE_DIR" >&2
  exit 2
fi
if [[ "$(stat -c %u "$HERMES_SCALE_DIR")" != "$HERMES_SCALE_UID" ]] || \
   [[ "$(stat -c %g "$HERMES_SCALE_DIR")" != "$HERMES_SCALE_GID" ]]; then
  echo "error: data directory owner does not match configured UID/GID" >&2
  exit 2
fi

was_running=false
if docker container inspect "$collector_container" >/dev/null 2>&1; then
  if [[ "$(docker inspect --format '{{.State.Running}}' "$collector_container")" == true ]]; then
    was_running=true
    docker stop "$collector_container" >/dev/null
  fi
fi

restart_collector() {
  local exit_code=$?
  if [[ "$was_running" == true ]]; then
    docker start "$collector_container" >/dev/null || true
  fi
  exit "$exit_code"
}
trap restart_collector EXIT

echo "Rebuilding sessions from SQLite without deleting raw measurements..."
docker run --rm \
  --name "$rebuild_container" \
  --user "$HERMES_SCALE_UID:$HERMES_SCALE_GID" \
  --read-only \
  --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --mount "type=bind,src=$config_file,dst=/etc/hermes-s7/config.toml,readonly" \
  --mount "type=bind,src=$HERMES_SCALE_DIR,dst=/data/scale" \
  --tmpfs /tmp:rw,noexec,nosuid,size=16m \
  "$collector_image" \
  rebuild-sessions --config /etc/hermes-s7/config.toml

echo "Session rebuild completed; raw measurements were retained."
