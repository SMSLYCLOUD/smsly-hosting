# Unikernel Orchestrator: The K8s Killer
# SMSLY Hosting — Paid Tier Feature

## Problem
Kubernetes is the industry standard for container orchestration, but it's absurdly complex for hosting:
- Minimum 3-node cluster: $60/mo before a single app runs
- Control plane overhead: ~500MB RAM, ~0.5 CPU
- Learning curve: months to years
- YAML configuration: 500+ lines per service
- 99% of features unused for simple web hosting

## Solution: Single-Binary Orchestrator in Rust

A ~5000-line Rust binary that manages unikernel lifecycles. No etcd. No API server. No kubelet. No YAML.

### Configuration
```toml
# sites.toml — entire config for a hosted site

[sites.customer-abc]
domain = "example.com"
image = "images/abc-v42.img"       # Unikernel image
port = 8001
memory = "64M"
scaling = { min = 1, max = 5 }
env = { DATABASE_URL = "sqlite:///data/abc/db.sqlite3" }
ssl = "auto"                       # Let's Encrypt

[sites.customer-xyz]
domain = "xyz-store.com"
image = "images/xyz-v18.img"
port = 8002
memory = "128M"
scaling = { min = 1, max = 10 }
env = { DATABASE_URL = "sqlite:///data/xyz/db.sqlite3" }
ssl = "auto"
```

### CLI
```bash
smsly deploy example.com ./app/          # Build + deploy unikernel
smsly scale example.com --max 10         # Set max instances
smsly logs example.com --follow          # Stream logs
smsly rollback example.com               # Instant rollback to previous version
smsly status                             # Show all sites, health, resource usage
smsly cert renew example.com             # Force SSL renewal
```

### Comparison
| Feature | Kubernetes | SMSLY Orchestrator |
|:--------|:-----------|:-------------------|
| Binary size | ~1GB (all components) | ~10MB (single binary) |
| RAM overhead | ~500MB | ~20MB |
| Config per site | ~500 lines YAML | ~10 lines TOML |
| Boot time | 30-60s (pod) | <100ms (unikernel) |
| CLI commands to deploy | 5-10 | 1 |
| Learning curve | Months | Minutes |
| Cost (control plane) | $60/mo | $0 |

## Status: CONCEPT
## Estimated Effort: 4 sprints
## Language: Rust (for memory safety + performance)
