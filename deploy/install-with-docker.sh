#!/usr/bin/env bash
set -euo pipefail

if [[ $(id -u) -ne 0 ]]; then
  echo "error: run this installer through sudo" >&2
  exit 2
fi

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
project_dir=$(CDPATH= cd -- "$script_dir/.." && pwd)
env_file="$script_dir/.env"
config_file="$script_dir/config.toml"

if [[ ! -r "$env_file" || ! -r "$config_file" ]]; then
  echo "error: run deploy/configure-remote.sh first, or create deploy/.env and deploy/config.toml" >&2
  exit 2
fi

set -a
# shellcheck disable=SC1090
source "$env_file"
set +a

: "${MQTT_BIND_ADDRESS:?missing MQTT_BIND_ADDRESS}"
: "${MQTT_USERNAME:?missing MQTT_USERNAME}"
: "${MQTT_PASSWORD:?missing MQTT_PASSWORD}"
: "${HERMES_SCALE_DIR:?missing HERMES_SCALE_DIR}"
: "${HERMES_SCALE_UID:?missing HERMES_SCALE_UID}"
: "${HERMES_SCALE_GID:?missing HERMES_SCALE_GID}"
: "${HEALTH_PORT:?missing HEALTH_PORT}"

pai_auth_file="${PAI_AUTH_FILE:-$script_dir/pai-auth.json}"
pai_enabled=$(python3 - "$config_file" <<'PY'
import sys
import tomllib

with open(sys.argv[1], "rb") as handle:
    config = tomllib.load(handle)
print("true" if config.get("pai", {}).get("enabled", False) else "false")
PY
)

if [[ "$pai_enabled" == true ]]; then
  if [[ ! -e "$pai_auth_file" ]]; then
    echo "error: [pai].enabled=true but Pai auth file is missing: $pai_auth_file" >&2
    exit 2
  fi
  if [[ ! -r "$pai_auth_file" ]]; then
    echo "error: Pai auth file is not readable: $pai_auth_file" >&2
    exit 2
  fi
  if [[ "$(stat -c %a "$pai_auth_file")" != "600" ]]; then
    echo "error: Pai auth file must have mode 0600: $pai_auth_file" >&2
    exit 2
  fi
  if [[ "$(stat -c %u "$pai_auth_file")" != "$HERMES_SCALE_UID" ]] || \
     [[ "$(stat -c %g "$pai_auth_file")" != "$HERMES_SCALE_GID" ]]; then
    echo "error: Pai auth file owner must match HERMES_SCALE_UID/GID ($HERMES_SCALE_UID:$HERMES_SCALE_GID)" >&2
    exit 2
  fi
  pai_auth_mount=(--mount "type=bind,src=$pai_auth_file,dst=/etc/hermes-s7/pai-auth.json,readonly")
else
  pai_auth_mount=()
fi

if [[ ! -d "$HERMES_SCALE_DIR" ]]; then
  echo "error: data directory does not exist: $HERMES_SCALE_DIR" >&2
  exit 2
fi
if [[ $(stat -c %u "$HERMES_SCALE_DIR") != "$HERMES_SCALE_UID" ]] || \
   [[ $(stat -c %g "$HERMES_SCALE_DIR") != "$HERMES_SCALE_GID" ]]; then
  echo "error: data directory owner does not match configured UID/GID" >&2
  exit 2
fi

managed_label=com.openai.hermes-s7.managed=true
mqtt_container=hermes-s7-mqtt
collector_container=hermes-s7-collector
network_name=hermes-s7-net
volume_name=hermes-s7-mqtt-data
mqtt_image=hermes-s7-mqtt:0.1.0
collector_image=hermes-s7-collector:0.1.0

wait_for_health() {
  local container_name=$1
  local status
  for _attempt in $(seq 1 60); do
    status=$(docker inspect \
      --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' \
      "$container_name")
    if [[ "$status" == healthy ]]; then
      return 0
    fi
    if [[ "$status" == unhealthy ]]; then
      break
    fi
    sleep 2
  done
  echo "error: container did not become healthy: $container_name" >&2
  docker logs --tail 100 "$container_name" >&2 || true
  return 1
}

remove_managed_container() {
  local container_name=$1
  local label_value
  if ! docker container inspect "$container_name" >/dev/null 2>&1; then
    return 0
  fi
  label_value=$(docker inspect \
    --format '{{index .Config.Labels "com.openai.hermes-s7.managed"}}' \
    "$container_name")
  if [[ "$label_value" != true ]]; then
    echo "error: refusing to replace unmanaged container: $container_name" >&2
    exit 1
  fi
  docker rm --force "$container_name" >/dev/null
}

echo "Building pinned project images..."
docker build \
  --tag "$mqtt_image" \
  --file "$project_dir/deploy/Mosquitto.Dockerfile" \
  "$project_dir"
docker build \
  --tag "$collector_image" \
  --file "$project_dir/deploy/Dockerfile" \
  "$project_dir"

docker network inspect "$network_name" >/dev/null 2>&1 || \
  docker network create "$network_name" >/dev/null
docker volume inspect "$volume_name" >/dev/null 2>&1 || \
  docker volume create "$volume_name" >/dev/null

remove_managed_container "$collector_container"
remove_managed_container "$mqtt_container"

if ss -ltn | awk '{print $4}' | grep -Eq '(^|:)1883$'; then
  echo "error: host port 1883 is already in use" >&2
  exit 1
fi
if ss -ltn | awk '{print $4}' | grep -Eq "(^|:)${HEALTH_PORT}$"; then
  echo "error: host health port $HEALTH_PORT is already in use" >&2
  exit 1
fi

docker run --detach \
  --name "$mqtt_container" \
  --label "$managed_label" \
  --network "$network_name" \
  --restart unless-stopped \
  --security-opt no-new-privileges:true \
  --env MQTT_USERNAME="$MQTT_USERNAME" \
  --env MQTT_PASSWORD="$MQTT_PASSWORD" \
  --publish "$MQTT_BIND_ADDRESS:1883:1883" \
  --volume "$volume_name:/mosquitto/data" \
  "$mqtt_image" >/dev/null

wait_for_health "$mqtt_container"

docker run --detach \
  --name "$collector_container" \
  --label "$managed_label" \
  --network "$network_name" \
  --restart unless-stopped \
  --user "$HERMES_SCALE_UID:$HERMES_SCALE_GID" \
  --read-only \
  --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --env HERMES_SCALE_MQTT_HOST="$mqtt_container" \
  --env HERMES_SCALE_MQTT_PORT=1883 \
  --env HERMES_SCALE_MQTT_USERNAME="$MQTT_USERNAME" \
  --env HERMES_SCALE_MQTT_PASSWORD="$MQTT_PASSWORD" \
  --env HERMES_SCALE_STORAGE_ROOT=/data/scale \
  --mount "type=bind,src=$config_file,dst=/etc/hermes-s7/config.toml,readonly" \
  --mount "type=bind,src=$HERMES_SCALE_DIR,dst=/data/scale" \
  "${pai_auth_mount[@]}" \
  --publish "127.0.0.1:$HEALTH_PORT:8080" \
  --tmpfs /tmp:rw,noexec,nosuid,size=16m \
  --health-cmd \
    "python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3).read()\"" \
  --health-interval 15s \
  --health-timeout 5s \
  --health-retries 4 \
  --health-start-period 10s \
  "$collector_image" >/dev/null

wait_for_health "$collector_container"

echo "Hermes S7 services are healthy."
docker ps \
  --filter "name=hermes-s7-" \
  --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
curl --fail --silent --show-error \
  "http://127.0.0.1:$HEALTH_PORT/health"
echo
