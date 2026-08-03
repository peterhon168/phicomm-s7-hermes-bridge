FROM eclipse-mosquitto:2.1.2-alpine

COPY deploy/mosquitto.conf /mosquitto/config/mosquitto.conf
COPY deploy/mqtt-entrypoint.sh /usr/local/bin/hermes-s7-mqtt-entrypoint
COPY deploy/mqtt-healthcheck.sh /usr/local/bin/hermes-s7-mqtt-healthcheck

RUN chmod 0755 \
      /usr/local/bin/hermes-s7-mqtt-entrypoint \
      /usr/local/bin/hermes-s7-mqtt-healthcheck

HEALTHCHECK --interval=10s --timeout=5s --retries=6 --start-period=5s \
  CMD ["/usr/local/bin/hermes-s7-mqtt-healthcheck"]

ENTRYPOINT ["/usr/local/bin/hermes-s7-mqtt-entrypoint"]
CMD ["mosquitto", "-c", "/mosquitto/config/mosquitto.conf"]
