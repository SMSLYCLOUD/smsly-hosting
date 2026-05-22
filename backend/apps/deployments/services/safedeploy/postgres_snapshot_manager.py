from typing import Optional
import subprocess
import logging
import os
import re

logger = logging.getLogger(__name__)

_VALID_DB_NAME_RE = re.compile(r'^[a-zA-Z0-9_]+$')

def _validate_db_name(name: str) -> None:
    if not _VALID_DB_NAME_RE.match(name):
        raise ValueError(f"Invalid database name: {name!r}")

class PostgresSnapshotManager:
    def __init__(self, admin_db_url: Optional[str] = None):
        url = admin_db_url or os.environ.get('DATABASE_URL')
        if not url:
            raise ValueError("DATABASE_URL must be set to use PostgresSnapshotManager")
        self.admin_db_url = url

    def create_clone(self, source_db_name: str, clone_db_name: str) -> bool:
        _validate_db_name(source_db_name)
        _validate_db_name(clone_db_name)
        try:
            logger.info(f"Cloning DB {source_db_name} to {clone_db_name}")
            term_sql = f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '{source_db_name}' AND pid <> pg_backend_pid();"
            subprocess.run(['psql', self.admin_db_url, '-c', term_sql], check=False, capture_output=True)
            create_sql = f'CREATE DATABASE "{clone_db_name}" WITH TEMPLATE "{source_db_name}";'
            res = subprocess.run(['psql', self.admin_db_url, '-c', create_sql], check=True, capture_output=True, text=True)
            return True
        except Exception as e:
            logger.error(f"Failed to clone db: {str(e)}")
            return False

    def destroy_clone(self, clone_db_name: str) -> bool:
        _validate_db_name(clone_db_name)
        if 'prod' in clone_db_name.lower() or 'main' in clone_db_name.lower():
             logger.error(f"SECURITY BLOCK: Attempted to drop protected database name '{clone_db_name}'")
             return False
        try:
            term_sql = f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '{clone_db_name}';"
            subprocess.run(['psql', self.admin_db_url, '-c', term_sql], check=False, capture_output=True)
            drop_sql = f'DROP DATABASE IF EXISTS "{clone_db_name}";'
            subprocess.run(['psql', self.admin_db_url, '-c', drop_sql], check=True, capture_output=True)
            return True
        except Exception as e:
            return False

    def get_clone_url(self, clone_db_name: str) -> str:
        base_url = self.admin_db_url
        parts = base_url.split('/')
        return '/'.join(parts[:-1]) + f"/{clone_db_name}"
