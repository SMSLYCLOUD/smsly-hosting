# Edge Compute: Unikernels at the Edge
# SMSLY Hosting — Paid Tier Feature

## Problem
Traditional CDNs only cache static files. Dynamic requests still go to the origin server, adding 50-200ms latency for distant users.

Cloudflare Workers/Deno Deploy solve this partially but only support JavaScript/TypeScript with severe limitations (no filesystem, limited CPU time, no native dependencies).

## Solution: Full-App Edge Deployment

Deploy the customer's **entire application** as a unikernel to edge nodes. Not just caching — the full Django/FastAPI/Node app runs at every edge location.

### How It Works
```
User in Tokyo → Tokyo Edge Node (unikernel running their full app)
User in London → London Edge Node (same unikernel)
User in NYC → NYC Edge Node (same unikernel)

No origin server. Every node IS the origin.
```

### Edge Network (Phase 1: 5 PoPs)
| Region | Provider | Monthly Cost |
|:-------|:---------|:------------|
| US-East (NYC) | Hetzner/DigitalOcean | $20/mo |
| US-West (LAX) | Vultr | $20/mo |
| EU-West (Amsterdam) | Hetzner | $10/mo |
| Asia-East (Singapore) | DigitalOcean | $20/mo |
| Australia (Sydney) | Vultr | $20/mo |
| **Total** | | **$90/mo** |

Split across 100 pro customers = $0.90/customer/mo infrastructure cost.

### Data Consistency
- Writes → routed to primary region
- Reads → served from nearest edge (SQLite replica via LiteFS)
- Replication lag: <1 second (Litestream)
- Conflict resolution: last-write-wins with vector clocks

### Latency Improvement
| User Location | Traditional (US-East origin) | SMSLY Edge |
|:-------------|:---------------------------|:-----------|
| New York | 20ms | 5ms |
| London | 90ms | 10ms |
| Tokyo | 180ms | 15ms |
| Sydney | 220ms | 12ms |

## Status: CONCEPT
## Estimated Effort: 4 sprints
## Dependencies: Unikernel orchestrator, Litestream replication
