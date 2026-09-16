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
# Mirror resilience: same live-probed selection as the backend and
# monolithic Dockerfiles (official first, vendor-neutral fallbacks).
RUN cp /etc/apt/sources.list /etc/apt/sources.list.official 2>/dev/null || true; \
 cp -a /etc/apt/sources.list.d /etc/apt/sources.list.d.smsly-official 2>/dev/null || true; \
 _smsly_apt_restore_official() { \
   cp /etc/apt/sources.list.official /etc/apt/sources.list 2>/dev/null || true; \
   if [ -d /etc/apt/sources.list.d.smsly-official ]; then rm -rf /etc/apt/sources.list.d; cp -a /etc/apt/sources.list.d.smsly-official /etc/apt/sources.list.d; fi; \
 }; \
 _smsly_apt_rewrite() { \
   for _f in /etc/apt/sources.list /etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources; do \
     [ -f "$_f" ] || continue; \
     sed -i "s|https\\?://deb\\.debian\\.org/debian-security|$2|g; s|https\\?://deb\\.debian\\.org|$1|g; s|https\\?://security\\.debian\\.org/debian-security|$2|g" "$_f"; \
   done; \
 }; \
 _smsly_apt_updated_ok() { \
   _out="$(apt-get update 2>&1)"; _rc=$?; printf '%s\n' "$_out"; \
   [ $_rc -eq 0 ] && ! printf '%s\n' "$_out" | grep -qE "^(Err?:[^ ]*|E:|Err) "; \
 }; \
 _smsly_apt_mirror_ok=false; \
 for _spec in \
   "|" \
   "http://debian.mirrors.ovh.net|http://debian.mirrors.ovh.net/debian-security" \
   "http://mirrors.edge.kernel.org|http://mirrors.edge.kernel.org/debian-security" \
   "http://mirror.netcologne.de|http://mirror.netcologne.de/debian-security" \
 ; do \
   _main="${_spec%%|*}"; _sec="${_spec#*|}"; \
   if [ -n "$_main" ]; then _smsly_apt_restore_official; _smsly_apt_rewrite "$_main" "$_sec"; fi; \
   if _smsly_apt_updated_ok; then _smsly_apt_mirror_ok=true; break; fi; \
 done; \
 if [ "$_smsly_apt_mirror_ok" != "true" ]; then echo "ERROR: no reachable Debian mirror" >&2; exit 1; fi; \
 rm -rf /etc/apt/sources.list.official /etc/apt/sources.list.d.smsly-official; \
 apt-get install -y --no-install-recommends \
      python3 python3-pip python3-dev gcc libc6-dev curl ca-certificates \
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
