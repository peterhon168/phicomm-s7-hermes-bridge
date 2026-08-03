#!/bin/sh
set -eu

mosquitto_pub \
  -h 127.0.0.1 \
  -u "$MQTT_USERNAME" \
  -P "$MQTT_PASSWORD" \
  -q 0 \
  -t hermes-s7/health \
  -m ok
