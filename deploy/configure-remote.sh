#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
project_dir=$(CDPATH= cd -- "$script_dir/.." && pwd)
env_file="$script_dir/.env"
config_file="$script_dir/config.toml"
data_dir=${HERMES_SCALE_DIR:-"$HOME/.local/share/hermes-s7/scale"}
mqtt_bind_address=${MQTT_BIND_ADDRESS:-127.0.0.1}

mkdir -p "$data_dir"
chmod 0750 "$data_dir"

if [ ! -e "$env_file" ]; then
  mqtt_password=$(python3 -c 'import secrets; print(secrets.token_hex(24))')
  umask 077
  {
    printf 'MQTT_BIND_ADDRESS=%s\n' "$mqtt_bind_address"
    printf 'MQTT_USERNAME=s7\n'
    printf 'MQTT_PASSWORD=%s\n' "$mqtt_password"
    printf 'HERMES_SCALE_DIR=%s\n' "$data_dir"
    printf 'HERMES_SCALE_UID=%s\n' "$(id -u)"
    printf 'HERMES_SCALE_GID=%s\n' "$(id -g)"
    printf 'HEALTH_PORT=18087\n'
  } > "$env_file"
fi
chmod 0600 "$env_file"

if [ ! -e "$config_file" ]; then
  install -m 0644 "$script_dir/config.example.toml" "$config_file"
fi
install -m 0644 \
  "$project_dir/docs/DATA_CONTRACT.md" \
  "$data_dir/README_DATA_CONTRACT.md"

echo "Remote configuration ready:"
echo "  project: $project_dir"
echo "  data:    $data_dir"
echo "  secret:  $env_file (mode 600; value not displayed)"
