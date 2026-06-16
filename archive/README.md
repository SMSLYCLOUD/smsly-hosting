# archive/

This directory quarantines dead-code stubs that the SMSLY hosting repository
no longer builds, tests, or deploys. The directories under `archive/` remain
**tracked in git** so the prior history (PRs, code review, intent) is preserved,
but they are excluded from active builds and CI by the patterns in
`scripts/check_dead_code_quarantine.sh`.

See [`DEAD_CODE_QUARANTINE.md`](./DEAD_CODE_QUARANTINE.md) for the full table
of what was moved, why, and how to revive a stub if absolutely required.
