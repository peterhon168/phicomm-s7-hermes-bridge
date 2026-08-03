FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /opt/hermes-s7

COPY pyproject.toml README.md ./
COPY src ./src
COPY deploy/collector-entrypoint.sh /usr/local/bin/collector-entrypoint

RUN pip install --no-cache-dir . \
    && groupadd --gid 10001 hermes-s7 \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin hermes-s7 \
    && chmod 0755 /usr/local/bin/collector-entrypoint

USER 10001:10001

ENTRYPOINT ["collector-entrypoint"]
CMD ["run", "--config", "/etc/hermes-s7/config.toml"]
