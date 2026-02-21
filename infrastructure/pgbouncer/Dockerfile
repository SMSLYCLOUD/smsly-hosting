FROM alpine:3.21

RUN apk add --no-cache pgbouncer postgresql-client \
    && adduser -D -H -s /sbin/nologin pgb \
    && mkdir -p /etc/pgbouncer \
    && chown -R pgb:pgb /etc/pgbouncer

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 5432

USER pgb
ENTRYPOINT ["/entrypoint.sh"]
