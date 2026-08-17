#!/usr/bin/env python3
"""
Loki-to-File Log Bridge for CrowdSec

Periodically queries Loki for node Caddy access logs and writes them to
a local directory that CrowdSec watches for security analysis.

Environment variables:
  LOKI_URL         - Loki API URL (default: http://smsly-loki:3100)
  CROWDSEC_LOG_DIR - Directory to write logs for CrowdSec (default: /var/log/crowdsec/nodes)
  POLL_INTERVAL    - Seconds between Loki queries (default: 10)
  LOOKBACK         - How far back to query on first run (default: 5m)
"""
import json
import os
import sys
import time
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("loki-log-bridge")

LOKI_URL = os.environ.get("LOKI_URL", "http://smsly-loki:3100")
CROWDSEC_LOG_DIR = os.environ.get("CROWDSEC_LOG_DIR", "/var/log/crowdsec/nodes")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "10"))
LOOKBACK = os.environ.get("LOOKBACK", "5m")


def parse_lookback(lookback: str) -> timedelta:
    """Parse a lookback string like '5m', '1h', '30s' into a timedelta."""
    unit = lookback[-1]
    value = int(lookback[:-1])
    if unit == "s":
        return timedelta(seconds=value)
    elif unit == "m":
        return timedelta(minutes=value)
    elif unit == "h":
        return timedelta(hours=value)
    elif unit == "d":
        return timedelta(days=value)
    return timedelta(minutes=5)


def ns_to_rfc3339(ns: str) -> str:
    """Convert a nanosecond timestamp string to RFC3339."""
    ns_int = int(ns)
    dt = datetime.fromtimestamp(ns_int / 1e9, tz=timezone.utc)
    return dt.isoformat()


def query_loki(client: httpx.Client, start: str, end: str) -> list[dict]:
    """Query Loki for node Caddy access logs."""
    url = f"{LOKI_URL}/loki/api/v1/query_range"
    params = {
        "query": '{job="caddy-access"}',
        "start": start,
        "end": end,
        "limit": 5000,
        "direction": "forward",
    }
    try:
        resp = client.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        results = []
        for stream in data.get("data", {}).get("result", []):
            node_id = stream.get("stream", {}).get("node_id", "unknown")
            for ts, line in stream.get("values", []):
                results.append({
                    "node_id": node_id,
                    "timestamp_ns": ts,
                    "line": line,
                })
        return results
    except Exception as exc:
        log.warning("Loki query failed: %s", exc)
        return []


def write_logs(logs: list[dict], log_dir: Path) -> int:
    """Write log lines to per-node files. Returns number of lines written."""
    written = 0
    for entry in logs:
        node_id = entry["node_id"]
        # Sanitize node_id for filename
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in node_id)
        log_file = log_dir / f"node_{safe_name}.log"
        try:
            with open(log_file, "a") as f:
                f.write(entry["line"] + "\n")
                written += 1
        except Exception as exc:
            log.warning("Failed to write log for %s: %s", node_id, exc)
    return written


def main():
    log_dir = Path(CROWDSEC_LOG_DIR)
    log_dir.mkdir(parents=True, exist_ok=True)

    lookback_delta = parse_lookback(LOOKBACK)
    last_ts_ns = str(int((datetime.now(timezone.utc) - lookback_delta).timestamp() * 1e9))

    log.info("Starting Loki log bridge → %s", log_dir)
    log.info("Loki URL: %s, poll interval: %ds, lookback: %s", LOKI_URL, POLL_INTERVAL, LOOKBACK)

    with httpx.Client(timeout=30) as client:
        while True:
            now_ns = str(int(datetime.now(timezone.utc).timestamp() * 1e9))
            logs = query_loki(client, last_ts_ns, now_ns)
            if logs:
                written = write_logs(logs, log_dir)
                # Advance timestamp to the latest log entry
                max_ts = max(e["timestamp_ns"] for e in logs)
                last_ts_ns = str(int(max_ts) + 1)  # +1 ns to avoid duplicates
                log.info("Wrote %d log lines from Loki", written)
            else:
                log.debug("No new logs")
            time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
