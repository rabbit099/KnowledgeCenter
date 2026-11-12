# Caddy running as a non-root user. Listens on 8080/8443 inside the container
# (host ports 80/443 are mapped in docker-compose.yml), so no capabilities are needed.
FROM caddy:2.10.2-alpine
# The upstream binary carries the file capability cap_net_bind_service. With cap_drop: ALL
# the kernel refuses to exec such a file, and we do not need it (ports 8080/8443), so the
# binary is copied without extended attributes, which drops the capability.
RUN cp /usr/bin/caddy /usr/bin/caddy.nocap && mv /usr/bin/caddy.nocap /usr/bin/caddy \
    && addgroup -S -g 10002 caddy && adduser -S -u 10002 -G caddy caddy \
    && mkdir -p /data/caddy /config/caddy && chown -R caddy:caddy /data /config
COPY Caddyfile /etc/caddy/Caddyfile
USER caddy
