# Incident 2026-09-22 — Platform apex unreachable from outside (grey DNS)

## Summary

`trulay.site` timed out for all external clients for several hours while
every on-host check stayed green. Three stacked causes; no single one
would have caused an outage alone.

## Timeline (UTC, 2026-09-22)

- 18:53 (Sep 21) → 00:20 — pgcat-tenants ban storm; tenant DB traffic down.
- 00:20 — pooler hot-fix verified; backends recover.
- Daytime — routine deploys run; each deploy's DNS reconcile flips the
  apex A record orange → grey (`edge_proxy_records` default False).
- Users report `ERR_CONNECTION_TIMED_OUT`; on-host edge probes return
  HTTP 200 in <100ms the entire time.
- Proxy re-enabled manually in Cloudflare dashboard; external fetches
  move from transport-error to CF 522 (origin TCP still failing for some
  paths — transit loss under investigation separately).

## Root causes

1. **Silent DNS downgrade.** `ensure_dns_records()` reconciled records
   toward `_desired_proxied_state()`, which returned False for the apex
   because `PlatformConfig.edge_proxy_records` defaulted False and had
   no UI control. Every deploy / provision / domain-save re-greyed the
   apex. No log line, no alert.
2. **Origin lockdown did its job too well.** Host firewall drops all
   non-Cloudflare 80/443 (`drop-direct` rule). Correct posture with
   orange DNS; total blackout with grey DNS.
3. **No outside-in observer.** All health checks run on-host (loopback
   bypasses firewall, NAT, DNS, and TLS-SNI routing), so the dashboard
   stayed green throughout.

## Fixes (all in repo)

- `apps/domains/services/dns.py` — one-way ratchet: reconcile fixes IPs
  and upgrades grey→orange when desired, but never auto-downgrades
  orange→grey. Downgrade requires explicit operator action.
- `PlatformConfig.edge_proxy_records` default `False → True`
  (migration `0233`), exposed in Settings → Platform (Edge Shield card)
  and the domain-config API.
- `apps/domains/services/edge_watch.py` + beat `watch_platform_edge_task`
  (10 min): resolves the apex through public DNS (never the host
  resolver), compares against desire (CF edge space vs origin IP),
  performs a real TLS+SNI `GET /`, and self-heals drift back to orange
  via the stored Cloudflare token. Failure logs at ERROR.

## Verify after any edge change

1. Public DNS (not on-host): A record returns Cloudflare edge IPs
   (`104.x`/`172.67.x`), not the origin.
2. External fetch returns 2xx (not 522) — 522 means Cloudflare itself
   cannot TCP-connect to the origin.
3. `iptables -L INPUT` CF ACCEPT counters increment; `drop-direct`
   counter stays flat for legit traffic.
4. Edge watchdog beat runs green (logs `Edge watch ok`).

## Prevention checklist

- Keep `edge_proxy_records` ON; treat any off-toggle as an incident.
- Never rely on on-host probes alone for edge-impacting changes.
- Keep origin lockdown ON while proxied (it is what makes grey-DNS
  incidents fail closed instead of exposing the origin — but pair it
  with the watchdog so fail-closed is also *visible*).
