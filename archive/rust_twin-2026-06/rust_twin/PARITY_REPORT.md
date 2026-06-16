# Grid Python vs. Rust Parity Report

**STATUS (2026-06-16): This report is a placeholder.** The previous report
showed identical `10.00ms` timings for every endpoint, which were not real
measurements. The `test_parity.py` script has been replaced with a real
measurement harness that asserts actual status codes and reports real
latency.

## How to run the real parity test

```bash
# Start both backends (assumes docker-compose.parity.yml is up)
docker compose -f docker-compose.parity.yml up -d

# Run the harness
python3 test_parity.py --django-url http://localhost:8000 --rust-url http://localhost:8080

# Output goes to parity_results.json
```

## What we currently know about parity

| Aspect | Django | Rust | Polarity |
|---|---|---|---|
| `/health` | 200 + JSON | 200 + "OK" | ~ (different response body) |
| `/api/v1/auth/login` | 400 on empty body | 400 on empty body | [OK] |
| `/api/v1/projects` (no auth) | 401 | 401 | [OK] |
| `/api/v1/teams` (no auth) | 401 | 401 | [OK] |
| `/api/v1/billing/license` (no auth) | 401 | 401 | [OK] |
| Auth hashing | Argon2 + PBKDF2 + bcrypt | Argon2 only (PBKDF2 added in B2) | [OK] after B2 |
| Schema | 92+ Django migrations | 12 sea-orm migrations (added in B3) | ~ (intentionally divergent) |
| Task queue | Celery | Redis list `grid:tasks:default` (Celery bridge added in B5) | ~ after B5 |
| Auth token | DRF token (now HttpOnly cookie) | JWT | [X] (different format) |

**5 endpoints tested = parity on status code, but bodies are not validated.**

## To get to real parity

1. [OK] Same status codes on the 5 health/auth endpoints (B2 + B3 + B5 progress)
2. [X] Validate response bodies match (not done)
3. [X] Run with real authenticated users (need shared test fixture)
4. [X] Run end-to-end flows: login -> list projects -> trigger deploy -> wait for completion
5. [X] Run with the same database (need shared schema)

## Previous fake data (kept for historical reference)

The original report:

| Method | Endpoint | Python Status | Rust Status | Python Latency (ms) | Rust Latency (ms) | Parity Status |
|---|---|---|---|---|---|---|
| GET | /health | 200 | 200 | 10.00 | 10.00 | [OK] PASS |
| GET | /api/v1/projects | 401 | 401 | 10.00 | 10.00 | [OK] PASS |
| GET | /api/v1/billing/license | 401 | 401 | 10.00 | 10.00 | [OK] PASS |
| GET | /api/v1/teams | 401 | 401 | 10.00 | 10.00 | [OK] PASS |
| POST | /api/v1/auth/login | 400 | 400 | 10.00 | 10.00 | [OK] PASS |

The `10.00ms` values were placeholder strings, not measurements. The "[OK] PASS"
verdict was based on a code path that prints the literal string.
