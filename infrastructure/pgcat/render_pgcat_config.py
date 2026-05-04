#!/usr/bin/env python3
import os
import sys

def get_env_or_die(var_name):
    val = os.environ.get(var_name)
    if not val:
        print(f"ERROR: Missing required environment variable {var_name}", file=sys.stderr)
        sys.exit(1)
    return val

def main():
    # Primary DB config
    db_host = get_env_or_die("DB_HOST")
    db_port = os.environ.get("DB_PORT", "5432")
    db_user = get_env_or_die("DB_USER")
    db_password = get_env_or_die("DB_PASSWORD")
    db_name = get_env_or_die("DB_NAME")

    # PgCat Admin
    admin_user = os.environ.get("PGCAT_ADMIN_USERNAME", "pgcat_admin")
    admin_pass = os.environ.get("PGCAT_ADMIN_PASSWORD")
    if not admin_pass or admin_pass == "pgcat_admin":
        if os.environ.get("DJANGO_ENV", "production") == "production":
            print("ERROR: Insecure PGCAT_ADMIN_PASSWORD in production.", file=sys.stderr)
            sys.exit(1)
        else:
            admin_pass = "pgcat_admin"

    # Pools sizes
    app_pool_size = int(os.environ.get("PGCAT_APP_POOL_SIZE", "20"))
    worker_pool_size = int(os.environ.get("PGCAT_WORKER_POOL_SIZE", "5"))

    # Connection Budget Validation
    pg_max_conn = int(os.environ.get("POSTGRES_MAX_CONNECTIONS", "100"))
    reserved_conn = 5
    total_requested = app_pool_size + worker_pool_size + reserved_conn

    if total_requested > pg_max_conn:
        print(f"ERROR: Connection budget exceeded! Requested: {total_requested}, Postgres max: {pg_max_conn}", file=sys.stderr)
        sys.exit(1)

    toml_content = f"""[general]
host = "0.0.0.0"
port = 5432
admin_username = "{admin_user}"
admin_password = "{admin_pass}"
server_lifetime = 86400000
idle_timeout = 60000
dns_cache_enabled = true
dns_cache_ttl = 30000

[pools.smsly_hosting]
pool_mode = "transaction"

[pools.smsly_hosting.shards.0]
servers = [["{db_host}", {db_port}, "primary"]]
database = "{db_name}"

[pools.smsly_hosting.users.{db_user}]
username = "{db_user}"
pool_size = {app_pool_size}
password = "{db_password}"

[pools.smsly_hosting_session]
pool_mode = "session"

[pools.smsly_hosting_session.shards.0]
servers = [["{db_host}", {db_port}, "primary"]]
database = "{db_name}"

[pools.smsly_hosting_session.users.{db_user}]
username = "{db_user}"
pool_size = {worker_pool_size}
password = "{db_password}"
"""

    out_path = sys.argv[1] if len(sys.argv) > 1 else "/etc/pgcat/pgcat.toml"
    with open(out_path, "w") as f:
        f.write(toml_content)

    print(f"Successfully rendered PgCat config to {out_path} with app pool={app_pool_size}, worker pool={worker_pool_size}")

if __name__ == "__main__":
    main()
