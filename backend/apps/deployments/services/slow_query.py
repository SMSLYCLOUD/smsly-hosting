"""Slow query monitoring — queries pg_stat_statements for query performance data."""
import logging

from django.db import connection

logger = logging.getLogger(__name__)


def fetch_slow_queries(min_duration_ms: float = 100, limit: int = 50) -> list:
    """Query pg_stat_statements for slow queries, ordered by mean_time desc."""
    try:
        with connection.cursor() as cursor:
            # Ensure extension is loaded
            cursor.execute("CREATE EXTENSION IF NOT EXISTS pg_stat_statements")
            cursor.execute("""
                SELECT queryid, query, calls,
                       mean_exec_time AS mean_time_ms,
                       total_exec_time AS total_time_ms,
                       rows, shared_blks_hit, shared_blks_read,
                       stddev_exec_time, min_exec_time, max_exec_time
                FROM pg_stat_statements
                WHERE mean_exec_time > %s
                ORDER BY mean_exec_time DESC
                LIMIT %s
            """, [min_duration_ms, limit])
            columns = [col[0] for col in cursor.description]
            results = []
            for row in cursor.fetchall():
                entry = dict(zip(columns, row, strict=False))
                entry['query'] = (entry['query'] or '')[:2000]  # truncate for display
                entry['queryid'] = str(entry['queryid'])
                results.append(entry)
            return results
    except Exception as exc:
        logger.warning("Failed to query pg_stat_statements: %s", exc)
        return []


def fetch_query_stats() -> dict:
    """Return aggregate stats: total unique queries, total time, cache hit ratio."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("CREATE EXTENSION IF NOT EXISTS pg_stat_statements")
            cursor.execute("""
                SELECT
                    COUNT(*) AS unique_queries,
                    SUM(calls) AS total_calls,
                    ROUND(SUM(total_exec_time)::numeric, 2) AS total_time_ms,
                    CASE WHEN SUM(shared_blks_hit + shared_blks_read) > 0
                        THEN ROUND(100.0 * SUM(shared_blks_hit) /
                            NULLIF(SUM(shared_blks_hit + shared_blks_read), 0), 1)
                        ELSE 100.0
                    END AS cache_hit_ratio
                FROM pg_stat_statements
            """)
            row = cursor.fetchone()
            if row:
                return {
                    'unique_queries': row[0] or 0,
                    'total_calls': row[1] or 0,
                    'total_time_ms': float(row[2] or 0),
                    'cache_hit_ratio': float(row[3] or 100),
                }
    except Exception as exc:
        logger.warning("Failed to fetch query stats: %s", exc)
    return {'unique_queries': 0, 'total_calls': 0, 'total_time_ms': 0, 'cache_hit_ratio': 100}


def reset_pg_stat_statements():
    """Reset pg_stat_statements counter (admin action)."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_stat_statements_reset()")
        return True
    except Exception as exc:
        logger.warning("Failed to reset pg_stat_statements: %s", exc)
        return False
