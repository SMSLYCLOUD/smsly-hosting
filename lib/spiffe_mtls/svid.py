"""
SVID Loading and Rotation
=========================
Loads X.509 SVIDs from SPIRE agent (file-based or gRPC) and auto-rotates.
"""

import os
import ssl
import time
import threading
import logging
from pathlib import Path
from typing import Optional, Callable

logger = logging.getLogger(__name__)


class SvidLoader:
    """Loads SPIFFE SVIDs from file-based or gRPC sources."""

    def __init__(
        self,
        cert_path: Optional[str] = None,
        key_path: Optional[str] = None,
        bundle_path: Optional[str] = None,
        socket_path: Optional[str] = None,
    ):
        self.cert_path = cert_path or os.getenv(
            "SPIFFE_SVID_CERT_PATH", "/opt/spire/svids/cert.pem"
        )
        self.key_path = key_path or os.getenv(
            "SPIFFE_SVID_KEY_PATH", "/opt/spire/svids/key.pem"
        )
        self.bundle_path = bundle_path or os.getenv(
            "SPIFFE_BUNDLE_PATH", "/opt/spire/svids/bundle.pem"
        )
        self.socket_path = socket_path or os.getenv(
            "SPIFFE_ENDPOINT_SOCKET", "/opt/spire/run/agent.sock"
        )

    def load_cert(self) -> Optional[str]:
        """Load X.509 certificate (SVID) from file."""
        try:
            return Path(self.cert_path).read_text()
        except FileNotFoundError:
            logger.warning("SVID cert not found: %s", self.cert_path)
            return None

    def load_key(self) -> Optional[str]:
        """Load private key from file."""
        try:
            return Path(self.key_path).read_text()
        except FileNotFoundError:
            logger.warning("SVID key not found: %s", self.key_path)
            return None

    def load_bundle(self) -> Optional[str]:
        """Load trust bundle (root CA) from file."""
        try:
            return Path(self.bundle_path).read_text()
        except FileNotFoundError:
            logger.warning("Trust bundle not found: %s", self.bundle_path)
            return None

    def is_available(self) -> bool:
        """Check if SVID files exist."""
        return all(
            Path(p).exists()
            for p in [self.cert_path, self.key_path, self.bundle_path]
        )


class SvidRotator:
    """Background thread that monitors SVID files for rotation."""

    def __init__(
        self,
        loader: SvidLoader,
        check_interval: int = 60,
        on_rotate: Optional[Callable] = None,
    ):
        self.loader = loader
        self.check_interval = check_interval
        self.on_rotate = on_rotate
        self._last_mtime: float = 0
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self):
        """Start the background rotation monitor."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._monitor, daemon=True)
        self._thread.start()
        logger.info(
            "SVID rotator started (check_interval=%ds)", self.check_interval
        )

    def stop(self):
        """Stop the background rotation monitor."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("SVID rotator stopped")

    def _monitor(self):
        """Monitor SVID files for changes."""
        while self._running:
            try:
                cert_path = Path(self.loader.cert_path)
                if cert_path.exists():
                    mtime = cert_path.stat().st_mtime
                    if mtime > self._last_mtime:
                        self._last_mtime = mtime
                        if self.on_rotate:
                            self.on_rotate()
                        logger.info("SVID rotated (new mtime=%f)", mtime)
            except Exception as e:
                logger.error("SVID rotation check failed: %s", e)
            time.sleep(self.check_interval)
