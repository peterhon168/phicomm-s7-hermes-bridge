#!/bin/sh
set -eu

config_path=/etc/hermes-s7/config.toml
data_path=/data/scale

if [ ! -r "$config_path" ]; then
  echo "error: collector cannot read $config_path" >&2
  exit 2
fi
if [ ! -d "$data_path" ]; then
  echo "error: collector data directory does not exist: $data_path" >&2
  exit 2
fi
if [ ! -w "$data_path" ]; then
  echo "error: collector UID/GID cannot write data directory: $data_path" >&2
  exit 2
fi

exec hermes-scale "$@"
