_MIN_FREE_MEMORY_MB = 256
_WAVE_RECHECK_SECONDS = 1800
_MAX_WAVE_RECHECKS = 30
_MAX_WAVE_SIZE = 5
_DEFAULT_WAVE_SIZE = 3
_VALID_PORT_RANGE = (1, 65535)
_MAX_CONCURRENT_BUILDS = 3
_ACTIVE_BUILDS_CACHE_KEY = "smsly:ecosystem:active_builds"
_BUILD_DEFER_SECONDS = 300
_DEFERRED_TASK_MAX_RETRIES = 5

_STACK_DEFAULT_PORTS = {
    "node": 3000,
    "nextjs": 3000,
    "nuxt": 3000,
    "ruby": 3000,
    "python": 8000,
    "django": 8000,
    "flask": 5000,
    "go": 8080,
    "rust": 8080,
    "java": 8080,
    "php": 8080,
    "elixir": 4000,
}

_ADDON_ENV_ALIASES = {
    "POSTGRES": ("POSTGRESQL_URL", "PG_URL", "POSTGRES_URL"),
    "REDIS": ("REDIS_URL", "REDIS_URI"),
    "MONGO": ("MONGO_URL", "MONGODB_URL"),
    "MYSQL": ("MYSQL_URL", "MARIADB_URL"),
    "RABBITMQ": ("RABBITMQ_URL", "AMQP_URL"),
    "MEILISEARCH": ("MEILI_URL", "MEILISEARCH_URL"),
}

_PLAN_REQUIRED_KEYS = frozenset({"services"})
_PLAN_OPTIONAL_KEYS = frozenset({"wave_size", "manifest", "name", "description", "addons", "deploy_order", "deploy_sequence", "metadata", "version"})

_SERVICE_REQUIRED_KEYS = frozenset({"name"})
_SERVICE_OPTIONAL_KEYS = frozenset({"build", "port", "depends_on", "skip", "env", "env_vars", "repo", "branch", "image", "addons", "stack", "deploy_order", "dockerfile", "cmd", "entrypoint", "volumes", "networks", "restart", "deploy", "labels"})
_SERVICE_VALID_BUILDS = frozenset({"nixpacks", "dockerfile", "image", "static", "docker-compose"})

_SMSLY_CORE_HINTS = frozenset({
    "smsly-core",
    "smsly-core-api",
    "smsly-platform-api",
    "smsly-platform",
    "smsly-core-platform",
})

_EXTERNAL_SECRETS = frozenset({
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "NPM_TOKEN",
    "PYPI_TOKEN",
    "DOCKER_TOKEN",
    "STRIPE_SECRET_KEY",
    "STRIPE_WEBHOOK_SECRET",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
})

_SECRET_HINTS = (
    "SECRET",
    "TOKEN",
    "PASSWORD",
    "PASSWD",
    "API_KEY",
    "PRIVATE_KEY",
    "CREDENTIAL",
)
