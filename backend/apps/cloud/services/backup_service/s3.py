"""S3/object-storage operations for backups."""
from __future__ import annotations

import logging
import time
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def _get_s3_client(endpoint='', region='us-east-1',
                   access_key='', secret_key=''):
    """Build a boto3 S3 client with the given credentials."""
    import boto3
    from botocore.client import Config
    kwargs = {'aws_access_key_id': access_key,
              'aws_secret_access_key': secret_key,
              'region_name': region,
              'config': Config(signature_version='s3v4', connect_timeout=30, read_timeout=60)}
    if endpoint:
        if not endpoint.startswith(('http://', 'https://')):
            endpoint = 'https://' + endpoint
        kwargs['endpoint_url'] = endpoint
    return boto3.client('s3', **kwargs)


def _s3_upload_with_retry(client, local_path, s3_bucket, s3_key,
                          max_retries=3, progress_callback=None) -> bool:
    """Upload a file to S3 with exponential backoff retry."""
    from botocore.s3.transfer import TransferConfig
    config = TransferConfig(
        multipart_threshold=8 * 1024 * 1024,
        max_concurrency=4,
        use_threads=True,
    )
    for attempt in range(1, max_retries + 1):
        try:
            extra_args = {}
            if progress_callback:
                extra_args['Callback'] = progress_callback
            client.upload_file(
                local_path, s3_bucket, s3_key,
                Config=config,
                **extra_args,
            )
            return True
        except Exception as exc:
            logger.warning(
                "S3 upload attempt %d/%d failed for %s/%s: %s",
                attempt, max_retries, s3_bucket, s3_key, exc,
            )
            if attempt < max_retries:
                time.sleep(2 ** attempt)
    return False


def _s3_delete_with_retry(client, s3_bucket, s3_key, max_retries=3) -> bool:
    """Delete an S3 object with exponential backoff retry."""
    for attempt in range(1, max_retries + 1):
        try:
            client.delete_object(Bucket=s3_bucket, Key=s3_key)
            return True
        except Exception as exc:
            logger.warning(
                "S3 delete attempt %d/%d failed for %s/%s: %s",
                attempt, max_retries, s3_bucket, s3_key, exc,
            )
            if attempt < max_retries:
                time.sleep(2 ** attempt)
    return False


def _s3_download_with_retry(client, s3_bucket, s3_key, local_path,
                            max_retries=3, progress_callback=None) -> bool:
    """Download a file from S3 with exponential backoff retry."""
    for attempt in range(1, max_retries + 1):
        try:
            extra_args = {}
            if progress_callback:
                extra_args['Callback'] = progress_callback
            client.download_file(
                s3_bucket, s3_key, local_path,
                **extra_args,
            )
            return True
        except Exception as exc:
            logger.warning(
                "S3 download attempt %d/%d failed for %s/%s: %s",
                attempt, max_retries, s3_bucket, s3_key, exc,
            )
            if attempt < max_retries:
                time.sleep(2 ** attempt)
    return False


def upload_backup_to_s3(local_path: str, s3_bucket: str, s3_key: str,
                        endpoint: str = '', region: str = 'us-east-1',
                        access_key: str = '', secret_key: str = '',
                        progress_callback=None) -> bool:
    """Upload a backup file to S3 (or R2/MinIO via custom endpoint) with retry."""
    try:
        client = _get_s3_client(endpoint, region, access_key, secret_key)
        ok = _s3_upload_with_retry(client, local_path, s3_bucket, s3_key, progress_callback=progress_callback)
        if ok:
            logger.info("Backup uploaded to s3://%s/%s", s3_bucket, s3_key)
        else:
            logger.error("S3 upload failed after retries for s3://%s/%s", s3_bucket, s3_key)
        return ok
    except ImportError:
        logger.warning("boto3 not available — S3 upload skipped")
    except Exception as exc:
        logger.error("S3 upload failed: %s", exc)
    return False


def download_from_s3(s3_bucket: str, s3_key: str, local_path: str,
                     endpoint: str = '', region: str = 'us-east-1',
                     access_key: str = '', secret_key: str = '',
                     progress_callback=None) -> bool:
    """Download a backup file from S3 (or R2/MinIO) to local path with retry."""
    try:
        client = _get_s3_client(endpoint, region, access_key, secret_key)
        ok = _s3_download_with_retry(client, s3_bucket, s3_key, local_path, progress_callback=progress_callback)
        if ok:
            logger.info("Backup downloaded from s3://%s/%s to %s", s3_bucket, s3_key, local_path)
        else:
            logger.error("S3 download failed after retries for s3://%s/%s", s3_bucket, s3_key)
        return ok
    except ImportError:
        logger.warning("boto3 not available — S3 download skipped")
    except Exception as exc:
        logger.error("S3 download failed: %s", exc)
    return False


def delete_cloud_backup_object(s3_bucket: str, s3_key: str,
                               endpoint: str = '', region: str = 'us-east-1',
                               access_key: str = '', secret_key: str = '') -> bool:
    """Delete a previously-uploaded backup object from S3 (or R2/MinIO) with retry."""
    try:
        client = _get_s3_client(endpoint, region, access_key, secret_key)
        ok = _s3_delete_with_retry(client, s3_bucket, s3_key)
        if ok:
            logger.info("Deleted s3://%s/%s", s3_bucket, s3_key)
        else:
            logger.error("S3 delete failed after retries for %s/%s", s3_bucket, s3_key)
        return ok
    except ImportError:
        logger.warning("boto3 not available — S3 delete skipped")
    except Exception as exc:
        logger.error("S3 delete failed for %s/%s: %s", s3_bucket, s3_key, exc)
    return False


def normalize_s3_key(s3_key: str, bucket: str | None = None) -> str:
    """Normalize an S3 key copied from various cloud dashboard formats."""
    key = s3_key.strip()

    if key.startswith('s3://'):
        key = key[5:]

    if key.startswith(('http://', 'https://')):
        parsed = urlparse(key)
        key = parsed.path.lstrip('/')

    key = key.lstrip('/')

    if bucket:
        while key.startswith(bucket + '/') or key == bucket:
            if key == bucket:
                key = ''
            else:
                key = key[len(bucket) + 1:]

    return key


def list_s3_objects(
    bucket: str,
    prefix: str = '',
    endpoint: str = '',
    region: str = 'us-east-1',
    access_key: str = '',
    secret_key: str = '',
    max_keys: int = 200,
) -> list[dict]:
    """List objects in an S3 bucket with the given prefix.

    Returns a list of dicts with 'key', 'size', 'last_modified'.
    Returns empty list on any error (connection, auth, etc).
    """
    try:
        client = _get_s3_client(endpoint, region, access_key, secret_key)
        kwargs = {'Bucket': bucket, 'MaxKeys': max_keys}
        if prefix:
            kwargs['Prefix'] = prefix
        response = client.list_objects_v2(**kwargs)
        contents = response.get('Contents', [])
        return [
            {
                'key': obj['Key'],
                'size': obj['Size'],
                'last_modified': obj['LastModified'].isoformat(),
            }
            for obj in contents
        ]
    except Exception as exc:
        logger.warning("Failed to list S3 objects in %s/%s: %s", bucket, prefix, exc)
        return []
