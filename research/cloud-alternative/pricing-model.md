# SMSLY Hosting Pricing Model
# Competitive Positioning vs Cloud Giants

## Current Market Pricing (2026)

### Vercel
| Plan | Price | Limits |
|:-----|:------|:-------|
| Hobby | Free | 100GB bandwidth, 1 project |
| Pro | $20/mo/member | 1TB bandwidth, unlimited projects |
| Enterprise | Custom | SLA, support, SSO |

### Netlify
| Plan | Price | Limits |
|:-----|:------|:-------|
| Starter | Free | 100GB bandwidth, 300 build min |
| Pro | $19/mo/member | 1TB bandwidth, SSO |
| Enterprise | Custom | SLA, support |

### AWS (Equivalent Stack)
| Service | Monthly Cost |
|:--------|:------------|
| EC2 (t3.medium) | $30 |
| RDS (db.t3.micro) | $30 |
| ALB | $22 |
| CloudFront | $10 |
| Route53 | $5 |
| ACM (SSL) | Free |
| CloudWatch | $10 |
| **Total** | **$107/mo** |

### Railway
| Plan | Price |
|:-----|:------|
| Hobby | $5/mo |
| Pro | $20/mo/member |
| Enterprise | Custom |

---

## SMSLY Hosting Pricing Strategy

### Principle: All-Inclusive, No Surprises

Everything is included in every plan. No bandwidth overages. No per-function charges. No database add-on. No monitoring add-on.

### Proposed Tiers

| | Free | Starter | Pro | Enterprise |
|:---|:---|:---|:---|:---|
| **Price** | $0 | $9/mo | $29/mo | $99/mo |
| **Sites** | 1 | 3 | 10 | Unlimited |
| **Bandwidth** | 10GB | 100GB | 1TB | Unlimited |
| **Database** | 100MB SQLite | 1GB SQLite | 10GB SQLite | 100GB SQLite |
| **DB Backups** | 7 days | 30 days | 90 days | 365 days |
| **Auto-scaling** | 1 instance | 3 instances | 10 instances | 50 instances |
| **SSL** | ✅ Auto | ✅ Auto | ✅ Auto + Wildcard | ✅ Custom certs |
| **Edge nodes** | 1 (nearest) | 1 | 3 | All 5 |
| **PHOTON** | Basic | Full | Full + API | Full + White-label |
| **Support** | Community | Email (48h) | Email (24h) | Priority (4h) |
| **Custom domain** | ✅ | ✅ | ✅ | ✅ |
| **Git deploy** | ✅ | ✅ | ✅ | ✅ |
| **Rollback** | Last 3 | Last 10 | Last 30 | Unlimited |

### Why This Wins

**vs Vercel ($20/mo):** SMSLY Pro ($29/mo) includes database, monitoring (PHOTON), auto-scaling, 10 sites. Vercel charges extra for all of these.

**vs AWS ($107/mo):** SMSLY Pro ($29/mo) replaces EC2 + RDS + ALB + CloudFront + CloudWatch. 73% cheaper.

**vs Railway ($20/mo):** SMSLY Starter ($9/mo) includes PHOTON intelligence that Railway doesn't have. 55% cheaper with more features.

### Revenue Projections

| Milestone | Customers | MRR |
|:----------|:----------|:----|
| Month 3 | 50 free, 10 starter | $90 |
| Month 6 | 200 free, 50 starter, 10 pro | $740 |
| Month 12 | 500 free, 150 starter, 40 pro, 5 enterprise | $3,005 |
| Month 24 | 2000 free, 500 starter, 150 pro, 20 enterprise | $10,830 |

### Infrastructure Cost Per Customer
| Tier | Revenue | Infra Cost | Margin |
|:-----|:--------|:-----------|:-------|
| Free | $0 | $0.50 (shared) | -$0.50 |
| Starter | $9 | $1.00 | 89% |
| Pro | $29 | $3.00 | 90% |
| Enterprise | $99 | $10.00 | 90% |

The key: unikernels use 6x less memory than Docker, so one VPS hosts 6x more customers. This is where the margin comes from.

## Status: CONCEPT
## Next Step: Implement Starter tier first, iterate pricing based on usage data
