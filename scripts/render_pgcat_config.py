#!/usr/bin/env python3
import os
import sys


def get_env_or_die(var_name):
    val = os.environ.get(var_name)
    if not val:
        print(f"ERROR: Missing required environment variable {var_name}", file=sys.stderr)
        sys.exit(1)
    return val


def _fetch_node_agent_users():
    """Query the database for lite-agent nodes and return their user/password pairs.

    Each node gets a dedicated database user created by the provisioner.
    The credentials are stored in the server's ``provider_metadata`` dict.

    Returns a list of ``(username, password)`` tuples.
    """
    try:
        db_host = os.environ.get("DB_HOST", "db")
        db_port = os.environ.get("DB_PORT", "5432")
        db_name = os.environ.get("DB_NAME", "smsly_hosting")
        db_user = os.environ.get("DB_USER", "smsly_admin")
        db_pass = os.environ.get("POSTGRES_PASSWORD", "")
        import psycopg2
        conn = psycopg2.connect(
            host=db_host, port=db_port, dbname=db_name,
            user=db_user, password=db_pass, connect_timeout=5,
        )
        conn.set_session(autocommit=True)
        cur = conn.cursor()
        cur.execute(
            "SELECT provider_metadata FROM deployments_managedserver "
            "WHERE provision_status = 'DONE' AND is_lite_agent = true"
        )
        users = []
        for row in cur.fetchall():
            meta = row[0] or {}
            node_user = meta.get("node_db_user")
            node_pass = meta.get("node_db_password")
            if node_user and node_pass:
                users.append((node_user, node_pass))
        cur.close()
        conn.close()
        return users
    except Exception as exc:
        print(f"WARNING: Could not query node agent users: {exc}", file=sys.stderr)
        return []


def main():
    db_host = get_env_or_die("DB_HOST")
    db_port = os.environ.get("DB_PORT", "5432")
    db_user = get_env_or_die("DB_USER")
    db_password = get_env_or_die("DB_PASSWORD")
    db_name = get_env_or_die("DB_NAME")

    admin_user = os.environ.get("PGCAT_ADMIN_USERNAME", "pgcat_admin")
    admin_pass = os.environ.get("PGCAT_ADMIN_PASSWORD")
    if not admin_pass or admin_pass == "pgcat_admin":
        if os.environ.get("DJANGO_ENV", "production") == "production":
            print("ERROR: Insecure PGCAT_ADMIN_PASSWORD in production.", file=sys.stderr)
            sys.exit(1)
        else:
            admin_pass = "pgcat_admin"

    app_pool_size = int(os.environ.get("PGCAT_APP_POOL_SIZE", "20"))
    worker_pool_size = int(os.environ.get("PGCAT_WORKER_POOL_SIZE", "5"))

    pg_max_conn = int(os.environ.get("POSTGRES_MAX_CONNECTIONS", "100"))
    reserved_conn = 5
    total_requested = app_pool_size + worker_pool_size + reserved_conn

    if total_requested > pg_max_conn:
        print(f"ERROR: Connection budget exceeded! Requested: {total_requested}, Postgres max: {pg_max_conn}", file=sys.stderr)
        sys.exit(1)

    node_users = _fetch_node_agent_users()

    lines = [
        '[general]',
        f'host = "0.0.0.0"',
        f'port = {db_port}',
        f'admin_username = "{admin_user}"',
        f'admin_password = "{admin_pass}"',
        'server_lifetime = 86400000',
        'idle_timeout = 60000',
        'dns_cache_enabled = true',
        'dns_cache_ttl = 30000',
        '',
        '[pools.smsly_hosting]',
        'pool_mode = "transaction"',
        '',
        '[pools.smsly_hosting.shards.0]',
        f'servers = [["{db_host}", {db_port}, "primary"]]',
        f'database = "{db_name}"',
        '',
        f'[pools.smsly_hosting.users.{db_user}]',
        f'pool_size = {app_pool_size}',
        f'password = "{db_password}"',
    ]

    for node_user, node_pass in node_users:
        lines += [
            '',
            f'[pools.smsly_hosting.users.{node_user}]',
            f'pool_size = 5',
            f'password = "{node_pass}"',
        ]

    lines += [
        '',
        '[pools.smsly_hosting_session]',
        'pool_mode = "session"',
        '',
        '[pools.smsly_hosting_session.shards.0]',
        f'servers = [["{db_host}", {db_port}, "primary"]]',
        f'database = "{db_name}"',
        '',
        f'[pools.smsly_hosting_session.users.{db_user}]',
        f'pool_size = {worker_pool_size}',
        f'password = "{db_password}"',
    ]

    for node_user, node_pass in node_users:
        lines += [
            '',
            f'[pools.smsly_hosting_session.users.{node_user}]',
            f'pool_size = 3',
            f'password = "{node_pass}"',
        ]

    toml_content = "\n".join(lines) + "\n"

    out_path = sys.argv[1] if len(sys.argv) > 1 else "/etc/pgcat/pgcat.toml"
    with open(out_path, "w") as f:
        f.write(toml_content)

    print(f"Successfully rendered PgCat config to {out_path} with app pool={app_pool_size}, worker pool={worker_pool_size}, node agents={len(node_users)}")


if __name__ == "__main__":
    main()
