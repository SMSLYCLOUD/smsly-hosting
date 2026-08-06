from __future__ import annotations

import contextlib
import logging
import re
from typing import Any

import sqlparse
from django.core.cache import cache
from sqlparse.sql import Statement

from apps.deployments.models.addons import Addon

logger = logging.getLogger(__name__)

_DISALLOWED_TOP_LEVEL = {
    'INSERT', 'UPDATE', 'DELETE', 'DROP', 'TRUNCATE', 'ALTER', 'CREATE',
    'GRANT', 'REVOKE', 'SET', 'COPY', 'VACUUM', 'REINDEX', 'CLUSTER',
    'LOCK', 'CALL', 'DO', 'EXECUTE', 'MERGE', 'REFRESH', 'LISTEN',
    'UNLISTEN', 'NOTIFY', 'DISCARD', 'RESET', 'SHOW',
}
_ALLOWED_TOP_LEVEL = {'SELECT', 'WITH', 'EXPLAIN', 'EXPLAIN ANALYZE'}

_REDIS_PATTERN_WILDCARDS = frozenset('*?[]')
_REDIS_RESPONSE_CAP = 1_048_576


def _validate_redis_keys_pattern(pattern: str) -> None:
    """Reject Redis key patterns that could enumerate the whole keyspace.

    A pattern like ``*`` or ``?`` lets a user scan every key in the addon's
    Redis.  Require a pattern containing at least 2 non-wildcard characters.
    """
    if pattern is None or not isinstance(pattern, str):
        raise ValueError("Redis key pattern must be a string")
    if not pattern.strip():
        raise ValueError("Redis key pattern must not be empty")
    non_wildcard = sum(1 for ch in pattern if ch not in _REDIS_PATTERN_WILDCARDS)
    if non_wildcard < 2:
        raise ValueError(
            "Redis key pattern must contain at least 2 non-wildcard characters"
        )


def _validate_readonly_sql(sql: str) -> str:
    """Validate that ``sql`` is a single read-only statement.

    Returns the cleaned SQL (with a single trailing semicolon) on success.
    Raises ``ValueError`` for anything that is not a ``SELECT`` / ``WITH``
    / ``EXPLAIN`` (or anything containing additional statements, DDL/DML
    keywords, or session/transaction configuration commands).
    """
    if sql is None:
        raise ValueError("SQL is required")
    if not isinstance(sql, str):
        raise ValueError("SQL must be a string")

    cleaned = sql.strip()
    if not cleaned:
        raise ValueError("SQL is required")

    stripped_for_check = cleaned.rstrip()
    if stripped_for_check.endswith(';'):
        stripped_for_check = stripped_for_check[:-1].rstrip()
    if ';' in stripped_for_check:
        raise ValueError("Multi-statement queries are not allowed")

    if re.search(r'\bSET\s+TRANSACTION\b', cleaned, re.IGNORECASE):
        raise ValueError("SET TRANSACTION is not allowed")
    if re.search(r'\bSET\s+SESSION\b', cleaned, re.IGNORECASE):
        raise ValueError("SET SESSION is not allowed")
    if re.search(r'\bSET\s+LOCAL\s+', cleaned, re.IGNORECASE):
        raise ValueError("SET LOCAL is not allowed")
    if re.search(r'\bSET\s+ROLE\b', cleaned, re.IGNORECASE):
        raise ValueError("SET ROLE is not allowed")
    if re.search(r'\bSET\s+CONSTRAINTS\b', cleaned, re.IGNORECASE):
        raise ValueError("SET CONSTRAINTS is not allowed")

    scan_sql = re.sub(
        r'\bFOR\s+(NO\s+KEY\s+|KEY\s+)?(UPDATE|SHARE)\b',
        '',
        cleaned,
        flags=re.IGNORECASE,
    )
    for kw in _DISALLOWED_TOP_LEVEL:
        if re.search(r'\b' + re.escape(kw) + r'\b', scan_sql, re.IGNORECASE):
            raise ValueError(f"Disallowed keyword: {kw}")

    statements = [s for s in sqlparse.parse(cleaned) if not _is_empty_stmt(s)]
    if not statements:
        raise ValueError("No SQL statement found")
    if len(statements) > 1:
        raise ValueError("Multi-statement queries are not allowed")

    stmt = statements[0]
    if not isinstance(stmt, Statement):
        raise ValueError("Unsupported SQL construct")

    first_keyword = _first_keyword(stmt)
    if first_keyword is None:
        raise ValueError("No SQL keyword detected")
    if first_keyword not in _ALLOWED_TOP_LEVEL:
        raise ValueError(
            f"Only SELECT/WITH/EXPLAIN queries are allowed (got {first_keyword})"
        )

    return cleaned


def _is_empty_stmt(stmt) -> bool:
    if stmt.ttype in (sqlparse.tokens.Comment, sqlparse.tokens.Whitespace):
        return True
    non_punct = [
        t for t in stmt.tokens
        if t.ttype is not sqlparse.tokens.Whitespace
        and t.ttype is not sqlparse.tokens.Comment
        and t.ttype is not sqlparse.tokens.Punctuation
    ]
    return not non_punct


def _first_keyword(stmt: Statement) -> str | None:
    for token in stmt.tokens:
        if token.ttype in sqlparse.tokens.Comment or token.ttype is sqlparse.tokens.Whitespace:
            continue
        if token.is_group and not token.tokens:
            continue
        value = token.value.strip().upper()
        if not value:
            continue
        match = re.match(r'([A-Z_]+)', value)
        if match:
            return match.group(1)
        return value.split()[0] if value.split() else None
    return None


class DatabaseProxy:
    """Connects to user addon databases through internal Docker network."""

    def __init__(self, addon: Addon):
        self.addon = addon
        self.connection_url = addon.connection_url

    def _build_pg_connection(proxy):
        import psycopg2
        return psycopg2.connect(proxy.connection_url, connect_timeout=10)

    def _build_redis_connection(proxy):
        import redis
        return redis.from_url(
            proxy.connection_url,
            decode_responses=True,
            socket_connect_timeout=10,
            socket_timeout=15,
        )

    def _build_mongo_connection(proxy):
        from pymongo import MongoClient
        return MongoClient(
            proxy.connection_url,
            serverSelectionTimeoutMS=10000,
            connectTimeoutMS=10000,
        )

    _CONNECTION_BUILDERS = {
        'POSTGRES': _build_pg_connection,
        'REDIS': _build_redis_connection,
        'MONGODB': _build_mongo_connection,
    }

    def get_connection(self) -> Any:
        """Create connection based on addon type."""
        builder = self._CONNECTION_BUILDERS.get(self.addon.addon_type)
        if not builder:
            raise ValueError(f"Unsupported addon type: {self.addon.addon_type}")
        return builder(self)

    # ── Postgres / MySQL ──
    def list_tables(self) -> list[dict]:
        """Returns table names."""
        if self.addon.addon_type != 'POSTGRES':
            return []

        try:
            conn = self.get_connection()
        except Exception:
            return []
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT table_name,
                           pg_size_pretty(pg_total_relation_size(quote_ident(table_name))),
                           (SELECT n_live_tup FROM pg_stat_user_tables WHERE relname = table_name)
                    FROM information_schema.tables
                    WHERE table_schema = 'public'
                """)
                return [{'name': r[0], 'size': r[1], 'rows': r[2]} for r in cur.fetchall()]
        finally:
            conn.close()

    def query(self, sql: str, limit: int = 100, *, addon: Addon | None = None, user=None) -> dict:
        """Execute read-only SQL query.

        ``addon`` and ``user`` are required for the ownership check that
        guarantees a user can only query databases they own. They are
        keyword-only so the call site is explicit.

        SECURITY (Issue 24): a per-addon Redis lock serialises
        concurrent queries so two simultaneous calls cannot both
        pass the throttle check and both open a session against
        the same addon.
        """
        if self.addon.addon_type != 'POSTGRES':
            return {}

        cleaned_sql = _validate_readonly_sql(sql)

        if addon is not None and user is not None:
            addon_owner_id = getattr(getattr(addon, 'service', None), 'owner_id', None)
            if addon_owner_id is None and hasattr(addon, 'owner_id'):
                addon_owner_id = addon.owner_id
            is_owner = (
                getattr(user, 'is_superuser', False)
                or (addon_owner_id is not None and addon_owner_id == getattr(user, 'id', None))
            )
            if not is_owner:
                raise PermissionError("You do not own this addon")

        lock_key = None
        if addon is not None:
            lock_key = f"db_proxy_lock:{addon.id}"
            if not cache.add(lock_key, "1", timeout=30):
                raise ValueError(
                    "Another query is in progress for this addon. Try again."
                )
        try:
            return self._execute_readonly(cleaned_sql, limit)
        finally:
            if lock_key is not None:
                with contextlib.suppress(Exception):
                    cache.delete(lock_key)

    def _execute_readonly(self, sql: str, limit: int) -> dict:
        """Open a connection, force READ ONLY at the SQL level, and run ``sql``."""
        conn = self.get_connection()
        try:
            conn.set_session(readonly=True)
            with conn.cursor() as cur:
                cur.execute("BEGIN ISOLATION LEVEL SERIALIZABLE READ ONLY")
                cur.execute("SET LOCAL statement_timeout = '5s'")
                cur.execute(sql)

                if cur.description:
                    columns = [desc[0] for desc in cur.description]
                    rows = cur.fetchmany(limit)
                    serializable_rows = []
                    for row in rows:
                        new_row = []
                        for cell in row:
                            if hasattr(cell, 'isoformat'):
                                new_row.append(cell.isoformat())
                            else:
                                new_row.append(cell)
                        serializable_rows.append(new_row)

                    return {'columns': columns, 'rows': serializable_rows, 'count': len(rows)}
                return {'affected': cur.rowcount}
        except PermissionError:
            raise
        except ValueError as e:
            return {'error': str(e)}
        except Exception as e:
            return {'error': str(e)}
        finally:
            try:
                conn.rollback()
            except Exception:
                logger.error("rollback failed on db proxy connection", exc_info=True)
            with contextlib.suppress(Exception):
                conn.close()

    # ── Redis ──
    def redis_info(self) -> dict:
        if self.addon.addon_type != 'REDIS':
            return {}
        r = self.get_connection()
        try:
            return r.info()
        finally:
            r.close()

    def redis_keys(self, pattern: str = '*', limit: int = 100) -> list:
        if self.addon.addon_type != 'REDIS':
            return []
        _validate_redis_keys_pattern(pattern)
        r = self.get_connection()
        try:
            keys = []
            for key in r.scan_iter(match=pattern, count=limit):
                keys.append(key)
                if len(keys) >= limit:
                    break
            return keys
        finally:
            r.close()

    @staticmethod
    def _cap_response_size(
        value: dict,
        cap: int = _REDIS_RESPONSE_CAP,
    ) -> tuple[dict, int, bool]:
        """Trim a dict to fit ``cap`` bytes of key+value content.

        Returns ``(value, total_size, truncated)`` where ``total_size`` is the
        untrimmed serialized size and ``truncated`` signals the cap was hit.
        """
        total_size = sum(len(str(k)) + len(str(v)) for k, v in value.items())
        if total_size <= cap:
            return value, total_size, False
        truncated: dict = {}
        size = 0
        for k, v in value.items():
            size += len(str(k)) + len(str(v))
            if size > cap:
                break
            truncated[k] = v
        return truncated, total_size, True

    def redis_get(self, key: str) -> dict:
        if self.addon.addon_type != 'REDIS':
            return {}
        r = self.get_connection()
        try:
            dtype = r.type(key)
            ttl = r.ttl(key)
            val = None
            if dtype == 'string':
                val = r.get(key)
            elif dtype == 'hash':
                raw = r.hgetall(key)
                value, total_size, truncated = self._cap_response_size(raw)
                if truncated:
                    return {
                        'key': key,
                        'type': dtype,
                        'ttl': ttl,
                        'truncated': True,
                        'total_size': total_size,
                        'data': value,
                    }
                val = value
            # Add other types as needed
            return {'key': key, 'type': dtype, 'ttl': ttl, 'value': val}
        finally:
            r.close()

    def get_stats(self) -> dict[str, Any]:
        """Database size, connections, uptime, memory usage."""
        if self.addon.addon_type == 'POSTGRES':
            try:
                conn = self.get_connection()
            except Exception:
                return {'status': 'OFFLINE'}
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT pg_size_pretty(pg_database_size(current_database()))")
                    size = cur.fetchone()[0]
                    cur.execute("SELECT count(*) FROM pg_stat_activity")
                    conns = cur.fetchone()[0]
                    return {
                        'size': size,
                        'connections': conns,
                        'connection_count': conns,
                        'status': 'ONLINE',
                    }
            except Exception:
                return {'status': 'OFFLINE'}
            finally:
                with contextlib.suppress(Exception):
                    conn.close()
        elif self.addon.addon_type == 'REDIS':
            try:
                r = self.get_connection()
            except Exception:
                return {'status': 'OFFLINE'}
            try:
                info = r.info('memory')
                clients = r.info('clients')
                connected_clients = clients.get('connected_clients')
                used_memory = info.get('used_memory') or 0
                maxmemory = info.get('maxmemory') or 0
                memory_usage_percent = None
                if maxmemory and maxmemory > 0 and used_memory is not None:
                    memory_usage_percent = round((used_memory / maxmemory) * 100, 1)
                return {
                    'used_memory_human': info.get('used_memory_human'),
                    'connections': connected_clients,
                    'connection_count': connected_clients,
                    'memory_usage_percent': memory_usage_percent,
                    'status': 'ONLINE'
                }
            except Exception:
                return {'status': 'OFFLINE'}
            finally:
                with contextlib.suppress(Exception):
                    r.close()
        return {}
