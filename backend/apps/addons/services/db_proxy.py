import logging
import json
from apps.deployments.models_addons import Addon

logger = logging.getLogger(__name__)

class DatabaseProxy:
    """Connects to user addon databases through internal Docker network."""

    def __init__(self, addon: Addon):
        self.addon = addon
        self.connection_url = addon.connection_url

    def get_connection(self):
        """Create connection based on addon type."""
        # Note: In production, this service would need to run in the same network
        # as the addons OR SSH tunnel to the host.
        # For simplicity in this implementation, we assume network reachability.
        if self.addon.addon_type == 'POSTGRES':
            import psycopg2
            return psycopg2.connect(self.connection_url)
        elif self.addon.addon_type == 'REDIS':
            import redis
            return redis.from_url(self.connection_url, decode_responses=True)
        elif self.addon.addon_type == 'MONGODB':
            from pymongo import MongoClient
            return MongoClient(self.connection_url)
        return None

    # ── Postgres / MySQL ──
    def list_tables(self) -> list[dict]:
        """Returns table names."""
        if self.addon.addon_type != 'POSTGRES':
            return []

        conn = self.get_connection()
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

    def query(self, sql: str, limit: int = 100) -> dict:
        """Execute read-only SQL query."""
        if self.addon.addon_type != 'POSTGRES':
            return {}

        conn = self.get_connection()
        conn.set_session(readonly=True) # Safety first
        try:
            with conn.cursor() as cur:
                # Enforce timeout
                cur.execute("SET statement_timeout = 10000")
                cur.execute(sql)

                if cur.description:
                    columns = [desc[0] for desc in cur.description]
                    rows = cur.fetchmany(limit)
                    # Convert non-serializable types
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
        except Exception as e:
            return {'error': str(e)}
        finally:
            conn.close()

    # ── Redis ──
    def redis_info(self) -> dict:
        if self.addon.addon_type != 'REDIS':
            return {}
        r = self.get_connection()
        return r.info()

    def redis_keys(self, pattern: str = '*', limit: int = 100) -> list:
        if self.addon.addon_type != 'REDIS':
            return []
        r = self.get_connection()
        keys = []
        for key in r.scan_iter(match=pattern, count=limit):
            keys.append(key)
            if len(keys) >= limit:
                break
        return keys

    def redis_get(self, key: str) -> dict:
        if self.addon.addon_type != 'REDIS':
            return {}
        r = self.get_connection()
        dtype = r.type(key)
        ttl = r.ttl(key)
        val = None
        if dtype == 'string':
            val = r.get(key)
        elif dtype == 'hash':
            val = r.hgetall(key)
        # Add other types as needed
        return {'key': key, 'type': dtype, 'ttl': ttl, 'value': val}

    def get_stats(self) -> dict:
        """Database size, connections, uptime, memory usage."""
        if self.addon.addon_type == 'POSTGRES':
            conn = self.get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT pg_size_pretty(pg_database_size(current_database()))")
                    size = cur.fetchone()[0]
                    cur.execute("SELECT count(*) FROM pg_stat_activity")
                    conns = cur.fetchone()[0]
                    return {'size': size, 'connections': conns, 'status': 'ONLINE'}
            except Exception:
                return {'status': 'OFFLINE'}
            finally:
                conn.close()
        elif self.addon.addon_type == 'REDIS':
            try:
                r = self.get_connection()
                info = r.info('memory')
                return {
                    'used_memory_human': info.get('used_memory_human'),
                    'connections': r.info('clients').get('connected_clients'),
                    'status': 'ONLINE'
                }
            except Exception:
                return {'status': 'OFFLINE'}
        return {}
