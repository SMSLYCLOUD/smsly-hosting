FROM alpine:3.21

RUN apk add --no-cache pgbouncer postgresql-client \
    && mkdir -p /etc/pgbouncer \
    && chown -R pgbouncer:pgbouncer /etc/pgbouncer

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 5432

USER pgbouncer
ENTRYPOINT ["/entrypoint.sh"]
