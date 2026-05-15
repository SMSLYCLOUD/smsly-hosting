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

        db_url = os.environ.get("DIRECT_DATABASE_URL") or os.environ.get("DATABASE_URL")
        if not db_url:
            self.stdout.write(self.style.WARNING("DATABASE_URL not set, skipping."))
            return

        try:
            conn = psycopg2.connect(db_url)
            conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Failed to connect to database: {e}"))
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
