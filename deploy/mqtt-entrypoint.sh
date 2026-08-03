#!/bin/sh
set -eu

case "${MQTT_USERNAME:-}" in
  ''|*[!A-Za-z0-9_-]*)
    echo "error: MQTT_USERNAME may contain only letters, digits, underscore and hyphen" >&2
    exit 2
    ;;
esac

if [ "${MQTT_PASSWORD:-}" = "replace-with-a-long-random-password" ] || \
   [ "${#MQTT_PASSWORD}" -lt 20 ]; then
  echo "error: MQTT_PASSWORD must be changed and contain at least 20 characters" >&2
  exit 2
fi

umask 077
if [ -e /mosquitto/data/password_file ]; then
  # The password file lives in the persistent volume.  Re-deployments must
  # update the configured account without asking mosquitto_passwd to create
  # an already-existing file (which exits with an error).
  mosquitto_passwd -b /mosquitto/data/password_file \
    "$MQTT_USERNAME" "$MQTT_PASSWORD"
else
  mosquitto_passwd -b -c /mosquitto/data/password_file \
    "$MQTT_USERNAME" "$MQTT_PASSWORD"
fi
printf 'user %s\ntopic readwrite device/zs7/#\ntopic write hermes-s7/health\n' \
  "$MQTT_USERNAME" > /mosquitto/data/acl_file
chown -R mosquitto:mosquitto /mosquitto/data
chmod 0600 /mosquitto/data/password_file
chmod 0640 /mosquitto/data/acl_file

exec "$@"
