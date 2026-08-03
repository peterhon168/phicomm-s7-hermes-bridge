#!/bin/sh
set -eu

: "${HERMES_SSH_HOST:?set HERMES_SSH_HOST to the remote Hermes host}"
: "${HERMES_SSH_USER:?set HERMES_SSH_USER to the remote account}"
: "${HERMES_SSH_KEY:?set HERMES_SSH_KEY to a dedicated private key path}"
: "${HERMES_SSH_KNOWN_HOSTS:?set HERMES_SSH_KNOWN_HOSTS to a pinned known_hosts file}"

exec ssh \
  -i "$HERMES_SSH_KEY" \
  -o IdentitiesOnly=yes \
  -o BatchMode=yes \
  -o UserKnownHostsFile="$HERMES_SSH_KNOWN_HOSTS" \
  -o StrictHostKeyChecking=yes \
  "$HERMES_SSH_USER@$HERMES_SSH_HOST" \
  "$@"
