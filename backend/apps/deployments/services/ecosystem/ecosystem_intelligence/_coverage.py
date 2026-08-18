import logging

logger = logging.getLogger(__name__)


def _ensure_100_percent_env_coverage(services: list[dict]):
    """
    Ensure every env var has a value. This is the LAST RESORT fallback.
    The AI should have filled everything intelligently from code analysis.
    Only external API keys get {{GENERATE}}.
    """
    _ADDON_URL_KEYS = {
        "DATABASE_URL", "POSTGRES_URL", "POSTGRES_DSN", "PGHOST",
        "REDIS_URL", "REDIS_HOST", "CACHE_URL", "CELERY_RESULT_BACKEND",
        "RABBITMQ_URL", "BROKER_URL", "CELERY_BROKER_URL", "AMQP_URL", "RABBITMQ_HOST",
        "QDRANT_URL", "QDRANT_HOST", "VECTOR_DB_URL",
        "MYSQL_URL", "MYSQL_HOST", "MARIADB_URL", "MARIADB_HOST",
        "MONGODB_URL", "MONGO_URL", "MONGO_URI", "MONGODB_HOST",
        "ELASTICSEARCH_URL", "ELASTICSEARCH_HOST", "ELASTIC_URL", "ELASTIC_HOST", "OPENSEARCH_URL",
        "MINIO_ENDPOINT", "MINIO_HOST", "S3_ENDPOINT_URL", "S3_HOST",
        "MEMCACHED_URL", "MEMCACHED_HOST", "MEMCACHE_SERVERS",
    }
    for svc in services:
        env_map = svc.get("env_vars", {})
        svc_port = str(svc.get("port") or 3000)

        for key in list(env_map.keys()):
            val = env_map.get(key)
            if not val or str(val).strip() in ("", "{{GENERATE}}", "{{FILL_ME}}") or str(val).startswith("REPLACE_WITH_"):
                if key in _ADDON_URL_KEYS:
                    continue

                key_upper = key.upper()
                from apps.cloud.services.build_constants import is_secret_env_var
                if is_secret_env_var(key_upper):
                    env_map[key] = "{{GENERATE}}"
                elif "CORS" in key_upper or "ORIGIN" in key_upper:
                    env_map[key] = f"http://localhost:{svc_port}"
                else:
                    # Do not generate random strings for non-secrets to prevent crashing
                    # typed configs (like numbers/ints). Leave empty to allow app defaults.
                    env_map[key] = ""

        svc["env_vars"] = env_map
