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
        # Query both the app metadata and the actual Postgres roles.
        # Using pg_roles as the source of truth avoids mismatches between
        # Django's server.id and the stored node_id in provider_metadata.
        cur.execute(
            "SELECT rolname FROM pg_roles WHERE rolname LIKE 'node_agent_%'"
        )
        node_role_names = {row[0] for row in cur.fetchall()}
        cur.execute(
            "SELECT provider_metadata FROM deployments_managedserver "
            "WHERE is_lite_agent = true AND provision_status IN ('DONE', 'PROVISIONING', 'FAILED')"
        )
        user_map = {}
        for row in cur.fetchall():
            meta = row[0] or {}
            node_pass = meta.get("node_db_password")
            node_user = meta.get("node_db_user")
            if not node_user:
                node_id = meta.get("node_id", "")
                prefix = node_id.split("-")[0] if node_id else ""
                if prefix:
                    node_user = f"node_agent_{prefix}"
            if node_user and node_pass:
                user_map[node_user] = node_pass
        # Add any node_agent_* roles in Postgres that don't have a metadata
        # entry yet (e.g. stale provisioning).  They'll work with the main
        # pool password as fallback.
        for role in node_role_names:
            if role not in user_map:
                user_map[role] = db_pass
        cur.close()
        conn.close()
        return list(user_map.items())
    except Exception as exc:
        print(f"WARNING: Could not query node agent users: {exc}", file=sys.stderr)
        return []


def main():
    db_host = get_env_or_die("DB_HOST")
    db_port = os.environ.get("DB_PORT", "5432")
    db_user = get_env_or_die("DB_USER")
    db_password = get_env_or_die("DB_PASSWORD")
    db_name = get_env_or_die("DB_NAME")

    # ── Read replica hosts (remote or local) ──────────────────────────────────
    # DB_REPLICA_HOSTS is a comma-separated list of "host:port" entries.
    # When set, pgcat routes SELECTs to these replicas and writes to the primary.
    # For maximum reliability, use remote replicas on a separate server.
    raw_replicas = os.environ.get("DB_REPLICA_HOSTS", "").strip()
    replica_servers = []
    if raw_replicas:
        for entry in raw_replicas.split(","):
            entry = entry.strip()
            if not entry:
                continue
            if ":" in entry:
                rhost, rport = entry.rsplit(":", 1)
                try:
                    rport = int(rport)
                except ValueError:
                    rport = 5432
            else:
                rhost, rport = entry, 5432
            replica_servers.append((rhost, rport))

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

    # Build the server list: primary + replicas
    primary_server = f'["{db_host}", {db_port}, "primary"]'
    replica_entries = [f'["{rh}", {rp}, "replica"]' for rh, rp in replica_servers]
    all_servers = [primary_server] + replica_entries
    servers_str = ",\n        ".join(all_servers)

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
        '# ── Query Parser (Read/Write Splitting) ──────────────────────────────────',
        'query_parser_enabled = true',
        'query_parser_read_write_splitting = true',
        'primary_reads_enabled = true',
        '',
        '# ── Health Checks ────────────────────────────────────────────────────────',
        'healthcheck_timeout = 5000',
        'healthcheck_delay = 30000',
        '',
        '# ── Banning ──────────────────────────────────────────────────────────────',
        'ban_time = 60',
        '',
        '[pools.smsly_hosting]',
        'pool_mode = "transaction"',
        '',
        '[pools.smsly_hosting.shards.0]',
        f'servers = [{servers_str}]',
        f'database = "{db_name}"',
    ]

    # PgCat expects users as a map keyed by username under the pool section
    lines.append(f'[pools.smsly_hosting.users.{db_user}]')
    lines.append(f'username = "{db_user}"')
    lines.append(f'pool_size = {app_pool_size}')
    lines.append(f'password = "{db_password}"')

    for node_user, node_pass in node_users:
        lines.append(f'[pools.smsly_hosting.users.{node_user}]')
        lines.append(f'username = "{node_user}"')
        lines.append(f'pool_size = 5')
        lines.append(f'password = "{node_pass}"')

    lines += [
        '',
        '[pools.smsly_hosting_session]',
        'pool_mode = "session"',
        '',
        '[pools.smsly_hosting_session.shards.0]',
        f'servers = [{servers_str}]',
        f'database = "{db_name}"',
        f'[pools.smsly_hosting_session.users.{db_user}]',
        f'username = "{db_user}"',
        f'pool_size = {worker_pool_size}',
        f'password = "{db_password}"',
    ]

    for node_user, node_pass in node_users:
        lines.append(f'[pools.smsly_hosting_session.users.{node_user}]')
        lines.append(f'username = "{node_user}"')
        lines.append(f'pool_size = 3')
        lines.append(f'password = "{node_pass}"')

    toml_content = "\n".join(lines) + "\n"

    out_path = sys.argv[1] if len(sys.argv) > 1 else "/etc/pgcat/pgcat.toml"
    with open(out_path, "w") as f:
        f.write(toml_content)

    print(f"Successfully rendered PgCat config to {out_path} with app pool={app_pool_size}, worker pool={worker_pool_size}, node agents={len(node_users)}, replicas={len(replica_servers)}")


if __name__ == "__main__":
    main()
