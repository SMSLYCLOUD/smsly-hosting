"""Deployment-related constants."""

# CPU/Resource conversion
CPU_MILLICORES_PER_CORE = 1024

# Health check defaults
DEFAULT_HEALTH_CHECK_INTERVAL = 30  # seconds
DEFAULT_HEALTH_CHECK_TIMEOUT = 300  # seconds
DEFAULT_HEALTH_CHECK_RETRIES = 90

# Deploy timeouts
DEPLOY_CONTAINER_TIMEOUT = 300  # seconds
DOCKER_BUILD_TIMEOUT = 300  # seconds
OLLAMA_PULL_TIMEOUT = 1800  # seconds

# Self-healing
SELF_HEAL_TTL = 3600  # seconds
SELF_HEAL_MAX_ATTEMPTS = 5
SELF_HEAL_SSH_TIMEOUT = 120  # seconds
SELF_HEAL_CONNECT_TIMEOUT = 30  # seconds
SELF_HEAL_COMMAND_TIMEOUT = 180  # seconds
SELF_HEAL_COOLDOWN = 20  # seconds

# Addon defaults
DEFAULT_ADDON_CPU = 0.5
DEFAULT_ADDON_MEMORY = 512  # MB

# Caddy reload cooldown
CADDY_RELOAD_COOLDOWN = 10  # seconds

# Service defaults
DEFAULT_INTERNAL_PORT = 8000
DEFAULT_CPU_TARGET = 80  # percent

# ── File I/O ────────────────────────────────────────────────────────────────
FILE_CHUNK_SIZE = 8192  # bytes — used for file reads, checksums, downloads

# ── Celery task time limits ─────────────────────────────────────────────────
# Each tier defines (soft_time_limit, time_limit) where time_limit = soft + buffer.
# soft_time_limit raises SoftTimeLimitExceeded; time_limit is the hard SIGKILL.

TASK_TIME_LIMIT_TRIVIAL = (30, 60)        # 30s   — commit status, heartbeat, election
TASK_TIME_LIMIT_QUICK = (120, 150)       # 2 min  — stall recovery, cleanup, heartbeat, failover monitor
TASK_TIME_LIMIT_STANDARD = (300, 360)    # 5 min  — delete service, replication health, snapshots, cron trigger
TASK_TIME_LIMIT_MEDIUM = (600, 660)      # 10 min — verify integrity, code intelligence, bundle deprovision
TASK_TIME_LIMIT_LONG = (900, 960)        # 15 min — mesh deploy, intelligence reports, registry GC
TASK_TIME_LIMIT_DATA_SYNC = (1200, 1260) # 20 min — replication deploy, bundle provision
TASK_TIME_LIMIT_DEPLOY = (3600, 3900)    # 1 hour — smart deploy, service backup/restore, scheduled ops
TASK_TIME_LIMIT_HEAVY = (7200, 7500)     # 2 hour — server backup/restore, platform update/rollback, purge
TASK_TIME_LIMIT_PROVISION = (1860, 1920) # 31 min — server provisioning (long SSH + install)

# ── Celery retry defaults ───────────────────────────────────────────────────
RETRY_DELAY_FAST = 30     # seconds — ecosystem scan, platform rollback
RETRY_DELAY_STANDARD = 60  # seconds — snapshot creation, bundle tasks
RETRY_DELAY_SLOW = 120     # seconds — purge user backups, cron trigger
RETRY_DELAY_HEAVY = 300    # seconds — service backup creation
RETRY_DELAY_BACKUP = 600   # seconds — server backup creation

# ── Stale task thresholds ───────────────────────────────────────────────────
STALL_RECOVERY_THRESHOLD_MINUTES = 10  # how long before a deletion is considered stalled
STALL_RECOVERY_BATCH_SIZE = 20         # max stalled deletions to re-queue per cycle

# ── Blue-green rollback grace period ────────────────────────────────────────
# Rollback containers younger than this are ignored by the stale scanner.
DEFAULT_ROLLBACK_GRACE_MINUTES = 10
