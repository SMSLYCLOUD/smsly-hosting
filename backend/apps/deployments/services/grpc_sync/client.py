"""gRPC client for pushing master DB backups to agents."""
import hashlib
import hmac as hmac_mod
import logging
import secrets
import time

import grpc

logger = logging.getLogger(__name__)

try:
    import db_sync_pb2
    import db_sync_pb2_grpc
except ImportError:
    db_sync_pb2 = None
    db_sync_pb2_grpc = None


def _create_mtls_channel(target_address: str, cert_path: str, key_path: str, ca_path: str):
    """Create a gRPC channel with mutual TLS."""
    with open(key_path, 'rb') as f:
        client_key = f.read()
    with open(cert_path, 'rb') as f:
        client_cert = f.read()
    with open(ca_path, 'rb') as f:
        ca_cert = f.read()

    credentials = grpc.ssl_channel_credentials(
        root_cert=ca_cert,
        private_key=client_key,
        certificate_chain=client_cert,
    )
    return grpc.secure_channel(target_address, credentials)


def push_db_to_agent(
    target_wg_address: str,
    dump_path: str,
    source_wg_address: str,
    cert_path: str | None = None,
    key_path: str | None = None,
    ca_path: str | None = None,
    chunk_size: int = 4 * 1024 * 1024,
) -> bool:
    """Push a DB dump to an agent via gRPC with mTLS.

    Falls back to plain HTTP if gRPC certs are not configured.
    Returns True on success.
    """
    if not cert_path or not key_path or not ca_path:
        logger.warning("gRPC certs not configured for %s, falling back to HTTP", target_wg_address)
        return _push_db_to_agent_http(target_wg_address, dump_path, source_wg_address)

    body_hash = hashlib.sha256()
    with open(dump_path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            body_hash.update(chunk)
    body_hash = body_hash.hexdigest()
    timestamp = str(int(time.time()))

    channel = None
    try:
        channel = _create_mtls_channel(
            f"{target_wg_address}:50051",
            cert_path, key_path, ca_path,
        )
        stub = db_sync_pb2_grpc.DbSyncStub(channel)

        def chunk_iterator():
            with open(dump_path, 'rb') as f:
                offset = 0
                while True:
                    data = f.read(chunk_size)
                    if not data:
                        break
                    yield db_sync_pb2.SyncChunk(
                        data=data,
                        byte_offset=offset,
                        is_last=False,
                    )
                    offset += len(data)
            yield db_sync_pb2.SyncChunk(data=b'', byte_offset=offset, is_last=True)

        request = db_sync_pb2.SyncRequest(
            source_wg_address=source_wg_address,
            timestamp=int(timestamp),
            body_hash=body_hash,
        )

        response = stub.SyncMasterDb(request, timeout=600)
        channel.close()
        return response.success
    except grpc.RpcError as e:
        logger.warning("gRPC push to %s failed: %s, falling back to HTTP", target_wg_address, e)
        if channel:
            channel.close()
        return _push_db_to_agent_http(target_wg_address, dump_path, source_wg_address)


def _push_db_to_agent_http(target_wg_address: str, dump_path: str, source_wg_address: str) -> bool:
    """Fallback HTTP push (same as current implementation)."""
    import requests as req_lib
    from django.conf import settings

    secret = str(getattr(settings, 'GATEWAY_SECRET', '') or getattr(settings, 'SECRET_KEY', ''))
    url = f"http://{target_wg_address}/api/v1/transfers/incoming/db-backup/"

    with open(dump_path, 'rb') as f:
        raw_body = f.read()
    body_hash = hashlib.sha256(raw_body).hexdigest()
    timestamp = str(int(time.time()))
    nonce = secrets.token_hex(16)
    raw_sig = f"POST|/api/v1/transfers/incoming/db-backup/|{timestamp}|{nonce}|{body_hash}"
    signature = hmac_mod.new(secret.encode(), raw_sig.encode(), hashlib.sha256).hexdigest()

    try:
        resp = req_lib.post(
            url, data=raw_body,
            headers={
                'X-Gateway-Signature-V2': signature,
                'X-Request-Timestamp': timestamp,
                'X-Request-Nonce': nonce,
                'Content-Type': 'application/gzip',
            },
            timeout=600,
        )
        return resp.ok
    except Exception as e:
        logger.warning("HTTP push to %s failed: %s", target_wg_address, e)
        return False
