# Security and privacy

Please do not publish or report any of the following:

- Pai `appSecret`, access/refresh tokens, cookies or captured request bodies;
- MQTT passwords, SSH private keys, known-host files or host fingerprints;
- personal health records, SQLite/CSV/JSONL exports or unredacted logs;
- firmware backups, APKs, packet captures or other device artifacts containing
  identifiers.

Use an ignored local auth file with mode `0600` and environment variables for
deployment secrets. The public examples use placeholders only.

For a suspected vulnerability, open a private report to the repository
maintainer rather than posting credentials or a live endpoint in a public issue.
This project is intended for devices and accounts owned by the operator; do
not use it to access another person's account or service.
