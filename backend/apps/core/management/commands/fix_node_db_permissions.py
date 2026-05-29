"""
Management command to fix node agent database permissions.

Ensures all node_agent_* users have proper access to all tables,
including tables created by recent migrations.

Usage:
    python manage.py fix_node_db_permissions
"""
import os
from django.core.management.base import BaseCommand
from django.conf import settings


class Command(BaseCommand):
    help = "Fix database permissions for all node agent users"

    def handle(self, *args, **options):
        self.stdout.write("Fixing node agent database permissions...")

        import psycopg2
        from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
        from psycopg2 import sql as pg_sql
        from urllib.parse import urlparse

        # Prefer DIRECT_DATABASE_URL (bypasses PgCat, uses admin credentials).
        # Fall back to DATABASE_URL, then construct from POSTGRES_* env vars.
        conn = None
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

        if not candidates:
            self.stdout.write(self.style.WARNING(
                "No database connection info found. Set DIRECT_DATABASE_URL or DATABASE_URL."
            ))
            return

        for label, url in candidates:
            try:
                parsed = urlparse(url)
                if parsed.hostname in ("pgcat", "pgbouncer", "haproxy"):
                    self.stdout.write(
                        self.style.WARNING(
                            f"Skipping {label} — points to pooler "
                            f"({parsed.hostname}), which may not have node_agent users. "
                            f"Set DIRECT_DATABASE_URL to bypass pooler."
                        )
                    )
                    continue
                conn = psycopg2.connect(url)
                conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
                self.stdout.write(f"Connected via {label} ({parsed.hostname}:{parsed.port})")
                break
            except Exception as e:
                err_msg = str(e)
                if "No pool configured" in err_msg or "No pool" in err_msg:
                    self.stdout.write(self.style.ERROR(
                        f"PgCat pool not configured for this node agent.\n"
                        f"  Error: {e}\n"
                        f"  Fix: On the Master node, run:\n"
                        f"    docker restart smsly-hosting-pgcat-1\n"
                        f"  Then re-run this update."
                    ))
                    return
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

        try:
            with conn.cursor() as cur:
                # Find all node agent users
                cur.execute(
                    "SELECT rolname FROM pg_roles WHERE rolname LIKE 'node_agent_%%'"
                )
                node_users = [row[0] for row in cur.fetchall()]

                if not node_users:
                    self.stdout.write(self.style.WARNING("No node agent users found."))
                    return

                self.stdout.write(f"Found {len(node_users)} node agent user(s): {', '.join(node_users)}")

                for username in node_users:
                    self.stdout.write(f"Fixing permissions for {username}...")
                    try:
                        # Grant schema access
                        cur.execute(
                            pg_sql.SQL("GRANT ALL PRIVILEGES ON SCHEMA public TO {}").format(
                                pg_sql.Identifier(username)
                            )
                        )
                        # Grant access to all existing tables
                        cur.execute(
                            pg_sql.SQL("GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO {}").format(
                                pg_sql.Identifier(username)
                            )
                        )
                        # Grant access to all existing sequences
                        cur.execute(
                            pg_sql.SQL("GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO {}").format(
                                pg_sql.Identifier(username)
                            )
                        )
                        # CRITICAL: Auto-grant permissions on future tables created by migrations
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
