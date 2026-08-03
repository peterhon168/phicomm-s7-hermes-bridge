# Contributing

Contributions should preserve the source-independent data model and the
privacy boundary.

Before opening a pull request:

```bash
.venv/bin/python -m unittest discover -s tests -v
bash -n deploy/install-with-docker.sh
sh -n deploy/collector-entrypoint.sh deploy/mqtt-entrypoint.sh
```

Do not include real Pai credentials, personal measurements, private network
paths, packet captures, firmware files or deployment secrets in commits. Add
fixtures with synthetic values and keep source-specific behavior behind an
adapter.
