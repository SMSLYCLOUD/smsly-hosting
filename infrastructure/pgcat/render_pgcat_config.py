#!/usr/bin/env python3
import os
import sys
import json

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
    node_app_pool_size = int(os.environ.get("PGCAT_NODE_APP_POOL_SIZE", "10"))
    node_worker_pool_size = int(os.environ.get("PGCAT_NODE_WORKER_POOL_SIZE", "5"))

    # Fetch node agent users and passwords from Master DB
    node_users = []
    try:
        import psycopg2
        conn = psycopg2.connect(
            host=db_host,
            port=db_port,
            user=db_user,
            password=db_password,
            database=db_name,
            connect_timeout=10
        )
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, provider_metadata FROM deployments_managedserver WHERE is_lite_agent = true"
            )
            rows = cur.fetchall()
            for row in rows:
                server_id, metadata = row
                if isinstance(metadata, str):
                    metadata = json.loads(metadata)
                node_db_password = (metadata or {}).get("node_db_password")
                if node_db_password:
                    node_id_short = str(server_id).split('-')[0]
                    node_username = f"node_agent_{node_id_short}"
                    node_users.append((node_username, node_db_password))
        conn.close()
        print(f"Found {len(node_users)} lite node agent(s) in database to configure pools for.")
    except Exception as e:
        print(f"WARNING: Could not fetch node agents from DB: {e}. Proceeding without node agent pools.", file=sys.stderr)

    # Connection Budget Validation
    pg_max_conn = int(os.environ.get("POSTGRES_MAX_CONNECTIONS", "100"))
    reserved_conn = 5
    total_requested = app_pool_size + worker_pool_size + reserved_conn
    for _ in node_users:
        total_requested += node_app_pool_size + node_worker_pool_size

    if total_requested > pg_max_conn:
        print(
            f"WARNING: Connection budget exceeded! "
            f"Requested: {total_requested}, Postgres max: {pg_max_conn}. "
            f"Consider increasing POSTGRES_MAX_CONNECTIONS or reducing pool sizes.",
            file=sys.stderr,
        )
        if os.environ.get("PGCAT_STRICT_CONNECTION_BUDGET", "").lower() in ("1", "true", "yes"):
            print("ERROR: PGCAT_STRICT_CONNECTION_BUDGET is set. Refusing to start.", file=sys.stderr)
            sys.exit(1)
        print("Proceeding with reduced pool sizes. Set PGCAT_STRICT_CONNECTION_BUDGET=1 to enforce.", file=sys.stderr)

    # Build user pools dynamically
    smsly_hosting_users = f"""[pools.smsly_hosting.users.{db_user}]
username = "{db_user}"
pool_size = {app_pool_size}
password = "{db_password}" """

    smsly_hosting_session_users = f"""[pools.smsly_hosting_session.users.{db_user}]
username = "{db_user}"
pool_size = {worker_pool_size}
password = "{db_password}" """

    for username, password in node_users:
        smsly_hosting_users += f"""

[pools.smsly_hosting.users.{username}]
username = "{username}"
pool_size = {node_app_pool_size}
password = "{password}" """

        smsly_hosting_session_users += f"""

[pools.smsly_hosting_session.users.{username}]
username = "{username}"
pool_size = {node_worker_pool_size}
password = "{password}" """

    toml_content = f"""[general]
host = "0.0.0.0"
port = 5432
admin_username = "{admin_user}"
admin_password = "{admin_pass}"
server_lifetime = 86400000
idle_timeout = 60000
connect_timeout = 5000
dns_cache_enabled = true
dns_cache_ttl = 30000

[pools.smsly_hosting]
pool_mode = "transaction"

[pools.smsly_hosting.shards.0]
servers = [["{db_host}", {db_port}, "primary"]]
database = "{db_name}"

{smsly_hosting_users}

[pools.smsly_hosting_session]
pool_mode = "session"

[pools.smsly_hosting_session.shards.0]
servers = [["{db_host}", {db_port}, "primary"]]
database = "{db_name}"

{smsly_hosting_session_users}
"""

    out_path = sys.argv[1] if len(sys.argv) > 1 else "/etc/pgcat/pgcat.toml"
    with open(out_path, "w") as f:
        f.write(toml_content)

    print(f"Successfully rendered PgCat config to {out_path} with app pool={app_pool_size}, worker pool={worker_pool_size}")

if __name__ == "__main__":
    main()
