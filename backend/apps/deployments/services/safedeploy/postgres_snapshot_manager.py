import logging
import os
import re
import subprocess
from types import SimpleNamespace
from urllib.parse import urlparse, urlunparse

from django.conf import settings

logger = logging.getLogger(__name__)

_VALID_DB_NAME_RE = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*$')

def _validate_db_name(name: str) -> None:
    if not _VALID_DB_NAME_RE.match(name):
        raise ValueError(f"Invalid database name: {name!r}")

def _mask_url_password(url: str) -> str:
    """Mask password in a database URL for safe logging."""
    try:
        parsed = urlparse(url)
        if parsed.password:
            netloc = f"{parsed.username}:****@{parsed.hostname}"
            if parsed.port:
                netloc += f":{parsed.port}"
            masked = parsed._replace(netloc=netloc)
            return urlunparse(masked)
    except Exception as exc:
        logger.debug("Failed to mask database URL: %s", exc)
    return url

class PostgresSnapshotManager:
    def __init__(self, admin_db_url: str | None = None):
        url = admin_db_url or os.environ.get('DIRECT_DATABASE_URL') or os.environ.get('DATABASE_URL')
        if not url:
            raise ValueError("DATABASE_URL or DIRECT_DATABASE_URL must be set to use PostgresSnapshotManager")

        # Bypass PgCat and connect directly to the database container for administrative queries
        try:
            parsed = urlparse(url)
            if parsed.hostname == 'pgcat':
                netloc = 'db'
                if parsed.port:
                    netloc = f"db:{parsed.port}"
                if parsed.username:
                    auth = parsed.username
                    if parsed.password:
                        auth += f":{parsed.password}"
                    netloc = f"{auth}@{netloc}"
                url = urlunparse(parsed._replace(netloc=netloc))
        except Exception as e:
            logger.warning(f"Failed to parse database URL for PgCat bypass: {e}")

        self.admin_db_url = url

    def _get_maintenance_url(self) -> str:
        """Return a URL connecting to the 'postgres' maintenance database.

        Uses the same server/credentials as the original URL, but replaces the
        database name with 'postgres' so that admin operations (CREATE/DROP
        DATABASE, pg_terminate_backend) do not compete for locks with the
        source/target databases themselves.
        """
        parsed = urlparse(self.admin_db_url)
        return urlunparse(parsed._replace(path='/postgres'))

    def _build_db_url(self, db_name: str) -> str:
        """Return a URL pointing to a specific database on the same server."""
        parsed = urlparse(self.admin_db_url)
        return urlunparse(parsed._replace(path=f'/{db_name}'))

    def _format_sql(self, composable) -> str:
        """Render a ``psycopg2.sql.Composable`` to a plain string.

        Uses a short-lived connection to the admin database purely as a
        formatting context for psycopg2's identifier / literal adapters.
        The connection is opened with a tight timeout and closed in a
        finally block.
        """
        import psycopg2
        conn = None
        try:
            conn = psycopg2.connect(
                self.admin_db_url,
                connect_timeout=5,
            )
            with conn.cursor() as cur:
                return composable.as_string(cur)
        except Exception:  # pylint: disable=broad-exception-caught
            # Fall back to a string representation if formatting fails
            # (e.g. the admin DB is temporarily unreachable). The call
            # sites all validate identifiers first, so this is safe.
            try:
                return str(composable)
            except Exception:  # pylint: disable=broad-exception-caught
                return ""
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # pylint: disable=broad-exception-caught
                    pass  # best-effort cleanup in finally block

    def _run_psql(self, db_url: str, sql: str, check: bool = True,
                  timeout: int = 120) -> SimpleNamespace:
        """Run a psql command and return a SimpleNamespace describing the outcome.

        The returned object always exposes ``.ok`` (bool). On success
        it carries ``.stdout`` / ``.stderr`` / ``.returncode`` from the
        underlying ``CompletedProcess``. On ``CalledProcessError`` or
        ``TimeoutExpired`` it carries ``.ok=False``, ``.error`` and
        ``.stderr`` so callers can log/display the failure without
        having to re-wrap the exception.
        """
        masked = _mask_url_password(db_url)
        logger.debug("psql %s: %s", masked, sql[:300])
        try:
            result = subprocess.run(
                ['psql', '-d', db_url, '-v', 'ON_ERROR_STOP=1', '-c', sql],
                check=check,
                capture_output=True,
                text=True,
                timeout=timeout
            )
        except subprocess.CalledProcessError as e:
            return SimpleNamespace(
                ok=False,
                error=str(e),
                stderr=e.stderr,
                stdout=e.stdout,
                returncode=e.returncode,
            )
        except subprocess.TimeoutExpired as e:
            stderr_str = (
                (e.stderr or b'').decode('utf-8', errors='replace')
                if isinstance(e.stderr, (bytes, bytearray)) else (e.stderr or '')
            )
            stdout_str = (
                (e.stdout or b'').decode('utf-8', errors='replace')
                if isinstance(e.stdout, (bytes, bytearray)) else (e.stdout or '')
            )
            return SimpleNamespace(
                ok=False,
                error=str(e),
                stderr=stderr_str,
                stdout=stdout_str,
                returncode=None,
            )
        return SimpleNamespace(
            ok=True,
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )

    def _run_psql_vars(self, db_url: str, sql: str, variables: dict,
                       check: bool = True, timeout: int = 120) -> SimpleNamespace:
        """Run a psql command with -v name=value parameters to avoid SQL injection.

        Same return contract as ``_run_psql`` — a ``SimpleNamespace``
        with ``.ok`` and on failure ``.error``/``.stderr`` so callers
        can compose multiple psql calls without each one having to
        re-wrap ``CalledProcessError``.
        """
        masked = _mask_url_password(db_url)
        logger.debug("psql %s: %s vars=%s", masked, sql[:300], list(variables.keys()))
        cmd = ['psql', '-d', db_url, '-v', 'ON_ERROR_STOP=1']
        for name, value in variables.items():
            cmd += ['-v', f'{name}={value}']
        cmd += ['-c', sql]
        try:
            result = subprocess.run(
                cmd,
                check=check,
                capture_output=True,
                text=True,
                timeout=timeout
            )
        except subprocess.CalledProcessError as e:
            return SimpleNamespace(
                ok=False,
                error=str(e),
                stderr=e.stderr,
                stdout=e.stdout,
                returncode=e.returncode,
            )
        except subprocess.TimeoutExpired as e:
            stderr_str = (
                (e.stderr or b'').decode('utf-8', errors='replace')
                if isinstance(e.stderr, (bytes, bytearray)) else (e.stderr or '')
            )
            stdout_str = (
                (e.stdout or b'').decode('utf-8', errors='replace')
                if isinstance(e.stdout, (bytes, bytearray)) else (e.stdout or '')
            )
            return SimpleNamespace(
                ok=False,
                error=str(e),
                stderr=stderr_str,
                stdout=stdout_str,
                returncode=None,
            )
        return SimpleNamespace(
            ok=True,
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )

    def create_clone(self, source_db_name: str, clone_db_name: str,
                     allow_production_disruption: bool = False) -> bool:
        _validate_db_name(source_db_name)
        _validate_db_name(clone_db_name)

        if allow_production_disruption:
            logger.critical(
                "ALLOW_PRODUCTION_DISRUPTION=True: pg_terminate_backend will be "
                "issued against %s to clone to %s", source_db_name, clone_db_name
            )
            try:
                from apps.deployments.models.audit import AuditLog
                AuditLog.objects.create(
                    actor='system',
                    action='DB_CLONE_PRODUCTION_DISRUPTION',
                    target=f"Service:{source_db_name}",
                    metadata={
                        'source_db': source_db_name,
                        'clone_db': clone_db_name,
                        'reason': 'allow_production_disruption=True',
                    },
                )
            except Exception as _audit_exc:
                logger.error("Failed to write AuditLog for production disruption: %s", _audit_exc)

        maintenance_url = self._get_maintenance_url()
        source_url = self._build_db_url(source_db_name)
        clone_url = self._build_db_url(clone_db_name)

        try:
            if settings.DEBUG:
                logger.error("start: source=%s clone=%s admin_url=%s",
                             source_db_name, clone_db_name, self.admin_db_url[:60])

            if allow_production_disruption:
                from psycopg2 import sql as pg_sql
                term_sql = (
                    "SELECT pg_terminate_backend(pid) "
                    "FROM pg_stat_activity "
                    "WHERE datname = :'source_db' "
                    "AND pid <> pg_backend_pid();"
                )
                term_res = self._run_psql_vars(
                    maintenance_url, term_sql,
                    variables={'source_db': source_db_name},
                    check=False,
                )
                if settings.DEBUG:
                    logger.error("terminate rc=%s stderr=%s",
                                 term_res.returncode, term_res.stderr[:200] if term_res.stderr else '')
                if term_res.returncode != 0:
                    logger.warning("pg_terminate_backend non-zero exit: %s",
                                   term_res.stderr.strip())
                else:
                    terminated = term_res.stdout.strip()
                    if terminated:
                        logger.info("Terminated %s backends on %s",
                                    terminated, source_db_name)

            from psycopg2 import sql as pg_sql
            drop_query = pg_sql.SQL("DROP DATABASE IF EXISTS {};").format(
                pg_sql.Identifier(clone_db_name)
            )
            drop_sql = self._format_sql(drop_query)
            drop_res = self._run_psql(maintenance_url, drop_sql, check=False)
            if settings.DEBUG:
                logger.error("drop rc=%s stderr=%s",
                             drop_res.returncode, drop_res.stderr[:200] if drop_res.stderr else '')
            if drop_res.returncode != 0:
                logger.warning("DROP IF EXISTS stderr: %s", drop_res.stderr.strip())

            create_query = pg_sql.SQL(
                "CREATE DATABASE {} WITH TEMPLATE {};"
            ).format(
                pg_sql.Identifier(clone_db_name),
                pg_sql.Identifier(source_db_name),
            )
            create_sql = self._format_sql(create_query)
            try:
                self._run_psql(maintenance_url, create_sql, check=True)
                if settings.DEBUG:
                    logger.error("TEMPLATE success")
                logger.info("Cloned %s → %s via TEMPLATE", source_db_name, clone_db_name)
                return True
            except subprocess.CalledProcessError as e:
                stderr_msg = e.stderr.strip() if e.stderr else '(empty)'
                stdout_msg = e.stdout.strip() if e.stdout else '(empty)'
                if settings.DEBUG:
                    logger.error("TEMPLATE FAILED: stderr=%s stdout=%s",
                                 stderr_msg[:300], stdout_msg[:300])
                logger.warning(
                    "CREATE DATABASE WITH TEMPLATE failed.\n"
                    "  stderr: %s\n"
                    "  stdout: %s",
                    stderr_msg, stdout_msg
                )

                logger.info("Attempting pg_dump / psql fallback …")
                return self._clone_via_dump(
                    source_db_name, clone_db_name,
                    source_url, clone_url, maintenance_url
                )

        except Exception as e:
            if settings.DEBUG:
                logger.error("UNEXPECTED EXCEPTION: %s", str(e), exc_info=True)
            logger.error("create_clone unexpected error: %s", str(e), exc_info=True)
            return False

    def _clone_via_dump(self, source_db_name: str, clone_db_name: str,
                        source_url: str, clone_url: str,
                        maintenance_url: str) -> bool:
        """Fallback: create an empty database, then pipe pg_dump into psql."""
        try:
            # Create empty database
            from psycopg2 import sql as pg_sql
            create_empty_query = pg_sql.SQL("CREATE DATABASE {};").format(
                pg_sql.Identifier(clone_db_name)
            )
            create_empty_sql = self._format_sql(create_empty_query)
            try:
                self._run_psql(maintenance_url, create_empty_sql, check=True)
            except subprocess.CalledProcessError as e:
                stderr_msg = e.stderr.strip() if e.stderr else '(empty)'
                logger.error(
                    "Fallback CREATE DATABASE also failed "
                    "(user likely lacks CREATEDB privilege).\n"
                    "  stderr: %s", stderr_msg
                )
                return False

            # Pipe pg_dump of source into the empty clone.
            # source_url already contains the db name in its path,
            # so do NOT pass source_db_name as a separate argument.
            dump_proc = subprocess.Popen(
                ['pg_dump', '-d', source_url, '--no-owner', '--no-acl'],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )
            restore_proc = subprocess.Popen(
                ['psql', '-d', clone_url, '-v', 'ON_ERROR_STOP=1'],
                stdin=dump_proc.stdout, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True
            )
            if dump_proc.stdout:
                dump_proc.stdout.close()

            _restore_stdout, restore_stderr = restore_proc.communicate(timeout=600)
            dump_proc.wait(timeout=60)

            if restore_proc.returncode == 0:
                logger.info("pg_dump fallback succeeded for %s", clone_db_name)
                return True
            else:
                logger.error(
                    "pg_dump fallback failed.\n"
                    "  restore stderr: %s",
                    restore_stderr.strip() if restore_stderr else '(empty)'
                )
                # Clean up the empty clone we created
                clean_query = pg_sql.SQL("DROP DATABASE IF EXISTS {};").format(
                    pg_sql.Identifier(clone_db_name)
                )
                clean_sql = self._format_sql(clean_query)
                self._run_psql(maintenance_url, clean_sql, check=False)
                return False
        except Exception as e:
            logger.error("pg_dump fallback unexpected error: %s",
                         str(e), exc_info=True)
            return False

    def create_empty_database(self, db_name: str) -> bool:
        """Create a fresh, empty database without cloning production data."""
        _validate_db_name(db_name)
        maintenance_url = self._get_maintenance_url()
        try:
            from psycopg2 import sql as pg_sql
            drop_query = pg_sql.SQL("DROP DATABASE IF EXISTS {};").format(
                pg_sql.Identifier(db_name)
            )
            drop_sql = self._format_sql(drop_query)
            self._run_psql(maintenance_url, drop_sql, check=False)

            create_query = pg_sql.SQL("CREATE DATABASE {};").format(
                pg_sql.Identifier(db_name)
            )
            create_sql = self._format_sql(create_query)
            self._run_psql(maintenance_url, create_sql, check=True)
            logger.info("Created empty preview database %s", db_name)
            return True
        except Exception as e:
            logger.error("create_empty_database error: %s", str(e), exc_info=True)
            return False

    def destroy_clone(self, clone_db_name: str) -> bool:
        _validate_db_name(clone_db_name)
        if 'prod' in clone_db_name.lower() or 'main' in clone_db_name.lower():
            logger.error(
                "SECURITY BLOCK: Attempted to drop protected database "
                "name '%s'", clone_db_name
            )
            return False
        try:
            from psycopg2 import sql as pg_sql
            maintenance_url = self._get_maintenance_url()
            term_sql = (
                "SELECT pg_terminate_backend(pid) "
                "FROM pg_stat_activity "
                "WHERE datname = :'clone_db';"
            )
            self._run_psql_vars(
                maintenance_url, term_sql,
                variables={'clone_db': clone_db_name},
                check=False,
            )
            drop_query = pg_sql.SQL("DROP DATABASE IF EXISTS {};").format(
                pg_sql.Identifier(clone_db_name)
            )
            drop_sql = self._format_sql(drop_query)
            self._run_psql(maintenance_url, drop_sql, check=True)
            return True
        except subprocess.CalledProcessError as e:
            logger.error("destroy_clone failed: stderr=%s", e.stderr.strip())
            return False
        except Exception as e:
            logger.error("destroy_clone error: %s", str(e), exc_info=True)
            return False

    def get_clone_url(self, clone_db_name: str) -> str:
        return self._build_db_url(clone_db_name)
