def build_addon_provisioning_requests(
    resolved_env: dict[str, str],
    service_name: str = "",
) -> list[str]:
    addons_needed: set[str] = set()
    for key, val in resolved_env.items():
        val_str = str(val).upper() if val else ""
        key_upper = key.upper()

        if (
            "{{POSTGRES_URL}}" in str(val)
            or "POSTGRESQL://" in val_str
            or "POSTGRES://" in val_str
            or ("DATABASE_URL" in key_upper and val)
            or ("POSTGRES_DSN" in key_upper and val)
        ):
            addons_needed.add("POSTGRES")

        if (
            "{{REDIS_URL}}" in str(val)
            or "REDIS://" in val_str
            or "_REDIS_URL" in key_upper
            or "REDIS_URL" in key_upper
            or "REDIS_URI" in key_upper
        ):
            addons_needed.add("REDIS")

        if (
            "{{RABBITMQ_URL}}" in str(val)
            or "AMQP://" in val_str
            or "RABBITMQ_URL" in key_upper
            or "CELERY_BROKER_URL" in key_upper
            or "AMQP_URL" in key_upper
            or "BROKER_URL" in key_upper
        ):
            addons_needed.add("RABBITMQ")

        if (
            "{{MINIO_URL}}" in str(val)
            or "MINIO_ENDPOINT" in key_upper
            or "S3_ENDPOINT_URL" in key_upper
        ):
            addons_needed.add("MINIO")

    return sorted(addons_needed)
