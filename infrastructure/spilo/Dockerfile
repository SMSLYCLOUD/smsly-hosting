# Self-hosted Spilo (Patroni + PostgreSQL 16)
# The upstream ghcr.io/zalando/spilo-16 images were removed, and flant/spilo
# tops out at PG11/Patroni 2.x — both unusable for a PG16 platform DB.
# This is a minimal equivalent: official postgres:16 + Patroni 3.x, with a
# tiny entrypoint that renders patroni.yml from the env vars the compose
# file already provides (SCOPE, ETCD3_HOST, POD_IP, PGUSER_SUPERUSER, ...).
FROM postgres:16

# Mirror resilience: some provider networks cannot reach Debian's official
# CDN (persistent TCP timeouts — 2026-09-17 fresh-install incident).
# Official first, then independent vendor mirrors (same snippet as the
# backend and monolithic Dockerfiles).
RUN cp /etc/apt/sources.list /etc/apt/sources.list.official 2>/dev/null || true; \
 _smsly_apt_fallback() { for _f in /etc/apt/sources.list /etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources; do [ -f "$_f" ] && sed -i "$1" "$_f"; done; }; \
 _smsly_apt_use_mirror() { case "$1" in \
   leaseweb) _smsly_apt_fallback 's|https\?://deb.debian.org|http://mirror.leaseweb.com|g; s|https\?://security.debian.org/debian-security|http://mirror.leaseweb.com/debian-security|g';; \
   ovh) _smsly_apt_fallback 's|http://mirror.leaseweb.com|http://debian.mirrors.ovh.net|g';; \
 esac; }; \
 for _m in official leaseweb ovh; do \
   [ "$_m" != "official" ] && _smsly_apt_use_mirror "$_m"; \
   apt-get update; \
   if apt-get install -y --no-install-recommends \
        python3 python3-pip python3-dev gcc libc6-dev curl ca-certificates; then break; fi; \
   [ "$_m" = "ovh" ] && exit 1; \
 done \
 && pip3 install --break-system-packages --no-cache-dir \
 && pip3 install --break-system-packages --no-cache-dir \
      "patroni[etcd3]==3.3.0" psycopg2-binary \
 && apt-get purge -y gcc libc6-dev python3-dev \
 && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

COPY entrypoint.sh /usr/local/bin/spilo-entrypoint.sh
RUN chmod +x /usr/local/bin/spilo-entrypoint.sh \
 && mkdir -p /var/lib/postgresql/data/patroni /var/log/postgresql \
 && chown -R postgres:postgres /var/lib/postgresql /var/log/postgresql

# Patroni REST API for HAProxy health checks
EXPOSE 5432 8008

ENTRYPOINT ["spilo-entrypoint.sh"]
