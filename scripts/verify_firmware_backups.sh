#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 3 ]]; then
  echo "usage: $0 ORIGINAL_A.bin ORIGINAL_B.bin EXPECTED_FLASH_BYTES" >&2
  exit 2
fi

backup_a=$1
backup_b=$2
expected_size=$3

case "$expected_size" in
  524288|1048576|2097152|4194304|8388608|16777216) ;;
  *)
    echo "error: EXPECTED_FLASH_BYTES must be a common ESP flash size in bytes" >&2
    exit 2
    ;;
esac

for backup in "$backup_a" "$backup_b"; do
  if [[ ! -f "$backup" || ! -s "$backup" ]]; then
    echo "error: backup is missing or empty: $backup" >&2
    exit 1
  fi
done

for backup in "$backup_a" "$backup_b"; do
  actual_size=$(wc -c < "$backup")
  actual_size=${actual_size//[[:space:]]/}
  if [[ "$actual_size" != "$expected_size" ]]; then
    echo "error: $backup has $actual_size bytes; expected $expected_size" >&2
    exit 1
  fi
done

if [[ "$backup_a" -ef "$backup_b" ]]; then
  echo "error: two distinct backup files are required; do not reuse one path or inode" >&2
  exit 1
fi

hash_backups() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum -- "$backup_a" "$backup_b"
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 -- "$backup_a" "$backup_b"
  else
    echo "error: neither sha256sum nor shasum is available" >&2
    return 1
  fi
}

if ! cmp -s -- "$backup_a" "$backup_b"; then
  echo "error: backups differ; do not flash anything" >&2
  hash_backups
  exit 1
fi

echo "OK: backups are byte-for-byte identical"
hash_backups
echo "Backup gate passed; separately copy one backup to another physical medium."
