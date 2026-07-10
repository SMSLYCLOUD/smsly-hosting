"""
Management command to fix node agent database permissions.

Ensures all node_agent_* users have proper access to all tables,
including tables created by recent migrations.

Self-healing: when running on a lite agent, detects stale node_agent
credentials and auto-resets the password via the admin backdoor
(DIRECT_DATABASE_URL). Also attempts to reload the master's PgCat
so the new pool config takes effect.

Usage:
    python manage.py fix_node_db_permissions
"""
import os

from django.core.management.base import BaseCommand


def _parse_db_user(url):
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        return parsed.username
    except Exception:
        return None


def _build_mesh_fallback():
    pg_pass = os.environ.get("POSTGRES_PASSWORD", "")
    if not pg_pass:
        return None
    pg_user = os.environ.get("POSTGRES_USER", "smsly_admin")
    pg_port = os.environ.get("POSTGRES_PORT", "5432")
    pg_db = os.environ.get("POSTGRES_DB", "smsly_hosting")
    mesh_ip = os.environ.get("MASTER_MESH_IP") or os.environ.get("MASTER_IP")
    if not mesh_ip:
        return None
    return f"postgresql://{pg_user}:{pg_pass}@{mesh_ip}:{pg_port}/{pg_db}"


def _is_pooler_host(hostname):
    return hostname and hostname.lower() in ("pgcat", "pgbouncer", "haproxy")


def _is_admin_user(username):
    return username and username.lower() in ("smsly_admin", "postgres")


def _extract_node_agent_from_url(url):
    user = _parse_db_user(url)
    if user and user.startswith("node_agent_"):
        return user
    return None


def _extract_node_password_from_url(url):
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        return parsed.password
    except Exception:
        return None


def _reload_pgcat(stdout, style):
    import subprocess
    try:
        result = subprocess.run(
            ["docker", "restart", "smsly-hosting-pgcat-1"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            stdout.write(style.SUCCESS("  Reloaded PgCat on master to pick up node agent pools."))
        else:
            stdout.write(style.WARNING(f"  PgCat restart returned {result.returncode}: {result.stderr.strip()}"))
    except FileNotFoundError:
        stdout.write(style.WARNING("  docker CLI not available; PgCat must be reloaded manually on master."))
    except Exception as e:
        stdout.write(style.WARNING(f"  Could not reload PgCat: {e}"))


class Command(BaseCommand):
    help = "Fix database node agent permissions and self-heal stale credentials"

    def handle(self, *args, **options):
        self.stdout.write("Fixing node agent database permissions...")

        from urllib.parse import urlparse

        import psycopg2
        from psycopg2 import sql as pg_sql
        from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

        conn = None
        connected_as_admin = False
        errors = []
        candidates = []

        direct_url = os.environ.get("DIRECT_DATABASE_URL")
        if direct_url:
            candidates.append(("DIRECT_DATABASE_URL", direct_url))

        db_url = os.environ.get("DATABASE_URL")
        if db_url and (not direct_url or db_url != direct_url):
            candidates.append(("DATABASE_URL", db_url))

        pg_host = os.environ.get("POSTGRES_HOST", "db")
        pg_port = os.environ.get("POSTGRES_PORT", "5432")
        pg_user = os.environ.get("POSTGRES_USER", "smsly_admin")
        pg_pass = os.environ.get("POSTGRES_PASSWORD", "")
        pg_db = os.environ.get("POSTGRES_DB", "smsly_hosting")
        if pg_pass:
            fallback_url = f"postgresql://{pg_user}:{pg_pass}@{pg_host}:{pg_port}/{pg_db}"
            candidates.append(("POSTGRES_* fallback", fallback_url))

        mesh_url = _build_mesh_fallback()
        if mesh_url:
            candidates.append(("POSTGRES_* via mesh IP", mesh_url))

        if not candidates:
            self.stdout.write(self.style.WARNING(
                "No database connection info found. Set DIRECT_DATABASE_URL or DATABASE_URL."
            ))
            return

        for label, url in candidates:
            try:
                parsed = urlparse(url)
                current_user = parsed.username
                is_admin = _is_admin_user(current_user)
                is_pooler = _is_pooler_host(parsed.hostname)

                if is_pooler and not is_admin:
                    self.stdout.write(
                        self.style.WARNING(
                            f"Skipping {label} — points to pooler "
                            f"({parsed.hostname}) without admin credentials. "
                            f"Set DIRECT_DATABASE_URL with smsly_admin to bypass pooler."
                        )
                    )
                    continue

                timeout = int(os.environ.get("DATABASE_CONNECT_TIMEOUT", 5))
                conn = psycopg2.connect(url, connect_timeout=timeout)
                conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
                if is_admin:
                    connected_as_admin = True
                    self.stdout.write(
                        f"Connected via {label} as admin ({parsed.hostname}:{parsed.port})"
                    )
                else:
                    self.stdout.write(
                        f"Connected via {label} ({parsed.hostname}:{parsed.port})"
                    )
                break
            except Exception as e:
                err_msg = str(e)
                if "No pool configured" in err_msg or "No pool" in err_msg:
                    self.stdout.write(self.style.WARNING(
                        f"PgCat pool not configured — will try admin fallback.\n"
                        f"  Error: {e}"
                    ))
                else:
                    errors.append(f"{label}: {e}")
                    self.stdout.write(
                        self.style.WARNING(f"Failed to connect via {label}: {e}")
                    )

        if conn is None:
            self.stdout.write(self.style.ERROR(
                "Could not connect to database with any available credentials:\n  "
                + "\n  ".join(errors)
                + "\n\n  Ensure DIRECT_DATABASE_URL is set to a direct PostgreSQL connection."
            ))
            return

        node_agent_user = _extract_node_agent_from_url(
            db_url or direct_url or ""
        )

        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT rolname FROM pg_roles WHERE rolname LIKE 'node_agent_%%'"
                )
                node_users = [row[0] for row in cur.fetchall()]

                if not node_users:
                    self.stdout.write(self.style.WARNING("No node agent users found."))
                    return

                self.stdout.write(
                    f"Found {len(node_users)} node agent user(s): {', '.join(node_users)}"
                )

                if connected_as_admin and node_agent_user:
                    if node_agent_user in node_users:
                        # SECURITY: Only sync password if it's non-empty and
                        # is NOT the admin password. Syncing a stale or empty
                        # password from the agent's env would poison the DB.
                        new_pass = _extract_node_password_from_url(db_url or direct_url or "")
                        if new_pass and new_pass != pg_pass:
                            cur.execute(
                                pg_sql.SQL("ALTER USER {} WITH PASSWORD %s").format(
                                    pg_sql.Identifier(node_agent_user)
                                ),
                                [new_pass],
                            )
                            self.stdout.write(
                                self.style.SUCCESS(
                                    f"  Self-healed: synced password for {node_agent_user}"
                                )
                            )
                            _reload_pgcat(self.stdout, self.style)
                        elif new_pass == pg_pass:
                            self.stdout.write(
                                self.style.WARNING(
                                    f"  Skipped password sync for {node_agent_user}: "
                                    f"URL password matches admin password (likely stale)"
                                )
                            )
                    else:
                        self.stdout.write(
                            self.style.WARNING(
                                f"Expected node agent user '{node_agent_user}' not found "
                                f"in pg_roles. Available: {', '.join(node_users) or '(none)'}"
                            )
                        )

                for username in node_users:
                    self.stdout.write(f"Fixing permissions for {username}...")
                    try:
                        cur.execute(
                            pg_sql.SQL("GRANT ALL PRIVILEGES ON SCHEMA public TO {}").format(
                                pg_sql.Identifier(username)
                            )
                        )
                        cur.execute(
                            pg_sql.SQL("GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO {}").format(
                                pg_sql.Identifier(username)
                            )
                        )
                        cur.execute(
                            pg_sql.SQL("GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO {}").format(
                                pg_sql.Identifier(username)
                            )
                        )
                        cur.execute(
                            pg_sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO {}").format(
                                pg_sql.Identifier(username)
                            )
                        )
                        cur.execute(
                            pg_sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO {}").format(
                                pg_sql.Identifier(username)
                            )
                        )
                        self.stdout.write(self.style.SUCCESS(f"  Fixed permissions for {username}"))
                    except Exception as e:
                        self.stdout.write(self.style.ERROR(f"  Failed to fix permissions for {username}: {e}"))

                self.stdout.write(self.style.SUCCESS("Node agent database permissions fixed."))
        finally:
            conn.close()
