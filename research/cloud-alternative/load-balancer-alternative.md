# Load Balancer Alternative: Unikernel Auto-Scaling
# SMSLY Hosting — Paid Tier Feature

## Problem
Traditional load balancers (Nginx LB, HAProxy, AWS ALB/NLB) are:
- Single points of failure
- Additional cost ($20-100/mo managed)
- Configuration overhead
- Slow scaling (spin up new VMs: 30s, containers: 2s)

## Solution: Spawn-on-Demand Architecture

Instead of routing traffic through a middleman, SMSLY spawns new unikernel instances when demand exceeds capacity.

### Architecture

```
┌─ Incoming Request ─────────────────────────────────┐
│                                                     │
│  ┌─────────────────────────────────────────────┐   │
│  │         SMSLY Request Router (Rust)          │   │
│  │                                             │   │
│  │  1. Receive request                         │   │
│  │  2. Check: is there a healthy instance?     │   │
│  │     YES → route to it (fastest path)        │   │
│  │     NO  → spawn new unikernel (<100ms)      │   │
│  │           then route to it                  │   │
│  │  3. Track: response_time per instance       │   │
│  │     if response_time > 200ms AND            │   │
│  │        active_requests > threshold          │   │
│  │     → spawn additional instance             │   │
│  │  4. Idle detection:                         │   │
│  │     if no requests for 5min → kill instance │   │
│  └─────────────────────────────────────────────┘   │
│                                                     │
│  ┌──────┐ ┌──────┐ ┌──────┐                       │
│  │ μK-1 │ │ μK-2 │ │ μK-3 │  ← auto-spawned      │
│  │ :8001│ │ :8002│ │ :8003│                        │
│  └──────┘ └──────┘ └──────┘                        │
└─────────────────────────────────────────────────────┘
```

### Scaling Rules
```toml
# smsly-hosting/config/scaling.toml

[scaling]
min_instances = 1
max_instances = 50
spawn_threshold_ms = 200      # Spawn new if p95 > 200ms
idle_timeout_s = 300           # Kill after 5min idle
health_check_interval_s = 10
boot_timeout_ms = 500          # Unikernel must boot in <500ms

[scaling.tiers]
free = { max_instances = 1 }
starter = { max_instances = 3 }
pro = { max_instances = 10 }
enterprise = { max_instances = 50 }
```

### Cost to Customer
| Tier | Traditional (LB + Servers) | SMSLY Hosting |
|:-----|:---------------------------|:--------------|
| Free | Not available | 1 instance, included |
| Starter ($9/mo) | $50/mo (LB + 2 servers) | 3 auto-scaling instances |
| Pro ($29/mo) | $150/mo (LB + 3 servers) | 10 auto-scaling instances |
| Enterprise ($99/mo) | $500+/mo | 50 instances + dedicated |

## Technical Requirements
- Rust-based request router (~500 lines)
- Unikernel lifecycle manager (spawn, health-check, kill)
- Metrics collector (response times, request counts)
- Integration with existing Nginx reverse proxy

## Status: CONCEPT
## Estimated Effort: 3 sprints
