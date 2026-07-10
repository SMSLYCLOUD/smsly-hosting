"""gRPC server for receiving master DB backups on agent nodes."""
import hashlib
import hmac as hmac_mod
import logging
import os
import time

import grpc
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

try:
    import db_sync_pb2
    import db_sync_pb2_grpc
except ImportError:
    db_sync_pb2 = None
    db_sync_pb2_grpc = None


class DbSyncServicer:
    """Servicer for receiving DB dumps from the master node."""

    MAX_UPLOAD_SIZE = 2 * 1024 * 1024 * 1024  # 2GB

    def SyncMasterDb(self, request, context):
        """Receive a streamed DB dump from the master."""
        timestamp = str(request.timestamp)
        body_hash = request.body_hash

        # Check timestamp freshness (5 minute window)
        try:
            req_time = int(timestamp)
            if abs(time.time() - req_time) > 300:
                context.abort(grpc.StatusCode.UNAUTHENTICATED, "Request timestamp expired")
        except (ValueError, TypeError):
            context.abort(grpc.StatusCode.UNAUTHENTICATED, "Invalid timestamp")

        # Receive all chunks
        dump_data = bytearray()
        for chunk in context.request_iterator:
            dump_data.extend(chunk.data)
            if len(dump_data) > self.MAX_UPLOAD_SIZE:
                context.abort(grpc.StatusCode.RESOURCE_EXHAUSTED, "Upload exceeds maximum size")

        if len(dump_data) < 100:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Empty or invalid backup data")

        # Verify body hash
        actual_hash = hashlib.sha256(bytes(dump_data)).hexdigest()
        if not hmac_mod.compare_digest(actual_hash, body_hash):
            context.abort(grpc.StatusCode.UNAUTHENTICATED, "Body hash mismatch")

        # Store the backup
        backup_dir = getattr(settings, 'DB_BACKUP_DIR', '/opt/smsly-hosting/backups/master-db')
        os.makedirs(backup_dir, exist_ok=True)
        timestamp_str = timezone.now().strftime('%Y%m%d_%H%M%S')
        dest_path = os.path.join(backup_dir, f'master_db_{timestamp_str}.sql.gz')

        try:
            with open(dest_path, 'wb') as f:
                f.write(dump_data)
        except OSError as e:
            logger.error("Failed to write DB backup: %s", e)
            context.abort(grpc.StatusCode.INTERNAL, "Failed to store backup")

        # Keep only 5 most recent
        try:
            existing = sorted(
                [os.path.join(backup_dir, f) for f in os.listdir(backup_dir) if f.endswith('.sql.gz')],
                key=os.path.getmtime,
            )
            while len(existing) > 5:
                old = existing.pop(0)
                os.remove(old)
        except OSError:
            pass

        logger.info("DB backup stored: %s (%d bytes)", dest_path, len(dump_data))
        return db_sync_pb2.SyncAck(
            success=True,
            bytes_received=len(dump_data),
        )


def serve(port: int = 50051):
    """Start the gRPC server for DB sync receiving."""
    if db_sync_pb2 is None:
        raise RuntimeError("db_sync_pb2 not found. Compile the proto file first.")

    server = grpc.server(grpc.thread_pool.ThreadPoolExecutor(max_workers=4))
    db_sync_pb2_grpc.add_DbSyncServicer_to_server(DbSyncServicer(), server)
    server.add_insecure_port(f'[::]:{port}')
    server.start()
    logger.info("DbSync gRPC server listening on port %d", port)
    return server
