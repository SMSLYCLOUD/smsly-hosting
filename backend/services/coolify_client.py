"""
Coolify API Client for SMSLY Hosting.

Integrates with Coolify self-hosted PaaS to handle:
- Application creation and deployment
- Database/Redis provisioning (addons)
- Environment variable management
- Deployment status tracking
"""
import logging
import httpx
from typing import Optional, Dict, Any, List
from django.conf import settings
from decouple import config

logger = logging.getLogger(__name__)


class CoolifyClient:
    """
    Client for Coolify API (v4).
    Documentation: https://coolify.io/docs/api-reference
    """
    
    def __init__(self):
        self.base_url = config('COOLIFY_API_URL', default='http://coolify:8000/api/v1')
        self.token = config('COOLIFY_API_TOKEN', default='')
        self.team_id = config('COOLIFY_TEAM_ID', default='0')
        self.headers = {
            'Authorization': f'Bearer {self.token}',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }
    
    # -------------------------------------------------------------------------
    # Application Management
    # -------------------------------------------------------------------------
    
    async def create_application(
        self,
        name: str,
        repository_url: str,
        branch: str = 'main',
        port: int = 8000,
        build_pack: str = 'dockerfile',
        environment_id: Optional[str] = None,
        env_vars: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Create a new application in Coolify.
        
        Args:
            name: Application name (must be unique)
            repository_url: Git repository URL
            branch: Git branch to deploy
            port: Internal port the app listens on
            build_pack: 'dockerfile', 'nixpacks', or 'buildpacks'
            environment_id: Coolify environment UUID (uses default if not set)
            env_vars: List of env vars [{"key": "FOO", "value": "bar", "is_secret": True}]
        
        Returns:
            dict with uuid, status, fqdn, etc.
        """
        try:
            payload = {
                "name": name,
                "git_repository": repository_url,
                "git_branch": branch,
                "build_pack": build_pack,
                "ports_exposes": str(port),
                "team_id": self.team_id,
            }
            
            if environment_id:
                payload["environment_id"] = environment_id
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.base_url}/applications",
                    headers=self.headers,
                    json=payload
                )
                response.raise_for_status()
                result = response.json()
                
                app_uuid = result.get("uuid")
                logger.info(f"Created Coolify application: {name} ({app_uuid})")
                
                # Inject environment variables if provided
                if env_vars and app_uuid:
                    await self.set_environment_variables(app_uuid, env_vars)
                
                return result
                
        except httpx.HTTPStatusError as e:
            logger.error(f"Coolify API error: {e.response.status_code} - {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"Failed to create Coolify application: {str(e)}")
            raise
    
    async def trigger_deployment(self, app_uuid: str, force: bool = False) -> Dict[str, Any]:
        """
        Trigger a new deployment for an existing application.
        
        Args:
            app_uuid: Application UUID in Coolify
            force: Force rebuild without cache
        
        Returns:
            dict with deployment_uuid, status
        """
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.base_url}/applications/{app_uuid}/deploy",
                    headers=self.headers,
                    json={"force": force}
                )
                response.raise_for_status()
                result = response.json()
                logger.info(f"Triggered deployment for app {app_uuid}")
                return result
        except Exception as e:
            logger.error(f"Failed to trigger deployment: {str(e)}")
            raise
    
    async def get_application(self, app_uuid: str) -> Dict[str, Any]:
        """Get application details including FQDN and status."""
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(
                    f"{self.base_url}/applications/{app_uuid}",
                    headers=self.headers
                )
                response.raise_for_status()
                return response.json()
        except Exception as e:
            logger.error(f"Failed to get application {app_uuid}: {str(e)}")
            raise
    
    async def delete_application(self, app_uuid: str) -> bool:
        """Delete an application from Coolify."""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.delete(
                    f"{self.base_url}/applications/{app_uuid}",
                    headers=self.headers
                )
                response.raise_for_status()
                logger.info(f"Deleted Coolify application: {app_uuid}")
                return True
        except Exception as e:
            logger.error(f"Failed to delete application {app_uuid}: {str(e)}")
            return False
    
    async def set_environment_variables(
        self,
        app_uuid: str,
        env_vars: List[Dict[str, Any]]
    ) -> bool:
        """
        Set environment variables for an application.
        
        Args:
            app_uuid: Application UUID
            env_vars: List of [{"key": "FOO", "value": "bar", "is_secret": bool}]
        """
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                for env_var in env_vars:
                    response = await client.post(
                        f"{self.base_url}/applications/{app_uuid}/envs",
                        headers=self.headers,
                        json={
                            "key": env_var["key"],
                            "value": env_var["value"],
                            "is_secret": env_var.get("is_secret", False),
                            "is_build_time": env_var.get("is_build_time", False),
                        }
                    )
                    response.raise_for_status()
                    
                logger.info(f"Set {len(env_vars)} env vars for app {app_uuid}")
                return True
        except Exception as e:
            logger.error(f"Failed to set env vars: {str(e)}")
            return False
    
    # -------------------------------------------------------------------------
    # Database / Addon Provisioning
    # -------------------------------------------------------------------------
    
    async def create_database(
        self,
        name: str,
        db_type: str,
        version: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Provision a new database instance.
        
        Args:
            name: Database name
            db_type: 'postgresql', 'mysql', 'mariadb', 'mongodb', 'redis'
            version: Optional version string (e.g., '15' for PostgreSQL 15)
        
        Returns:
            dict with uuid, internal_db_url, external_db_url
        """
        type_version_map = {
            "postgresql": version or "15",
            "mysql": version or "8.0",
            "mariadb": version or "10.11",
            "mongodb": version or "7.0",
            "redis": version or "7",
        }
        
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    f"{self.base_url}/databases",
                    headers=self.headers,
                    json={
                        "name": name,
                        "type": db_type,
                        "version": type_version_map.get(db_type, "latest"),
                        "team_id": self.team_id,
                        "is_public": False,
                    }
                )
                response.raise_for_status()
                result = response.json()
                logger.info(f"Created Coolify database: {name} ({db_type})")
                return result
        except Exception as e:
            logger.error(f"Failed to create database: {str(e)}")
            raise
    
    async def get_database(self, db_uuid: str) -> Dict[str, Any]:
        """Get database details including connection URL."""
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(
                    f"{self.base_url}/databases/{db_uuid}",
                    headers=self.headers
                )
                response.raise_for_status()
                return response.json()
        except Exception as e:
            logger.error(f"Failed to get database {db_uuid}: {str(e)}")
            raise
    
    async def delete_database(self, db_uuid: str) -> bool:
        """Delete a database instance."""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.delete(
                    f"{self.base_url}/databases/{db_uuid}",
                    headers=self.headers
                )
                response.raise_for_status()
                logger.info(f"Deleted Coolify database: {db_uuid}")
                return True
        except Exception as e:
            logger.error(f"Failed to delete database {db_uuid}: {str(e)}")
            return False
    
    # -------------------------------------------------------------------------
    # Deployment Status
    # -------------------------------------------------------------------------
    
    async def get_deployment_logs(
        self,
        app_uuid: str,
        deployment_uuid: Optional[str] = None
    ) -> str:
        """Fetch build/deployment logs."""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                url = f"{self.base_url}/applications/{app_uuid}/logs"
                if deployment_uuid:
                    url = f"{self.base_url}/deployments/{deployment_uuid}/logs"
                    
                response = await client.get(url, headers=self.headers)
                response.raise_for_status()
                return response.json().get("logs", "")
        except Exception as e:
            logger.error(f"Failed to fetch logs: {str(e)}")
            return ""
    
    # -------------------------------------------------------------------------
    # Sync Wrappers for Celery Tasks
    # -------------------------------------------------------------------------
    
    def create_application_sync(self, **kwargs) -> Dict[str, Any]:
        """Synchronous wrapper for Celery tasks."""
        import asyncio
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(self.create_application(**kwargs))
        finally:
            loop.close()
    
    def trigger_deployment_sync(self, app_uuid: str, force: bool = False) -> Dict[str, Any]:
        """Synchronous wrapper for Celery tasks."""
        import asyncio
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(self.trigger_deployment(app_uuid, force))
        finally:
            loop.close()
    
    def create_database_sync(self, **kwargs) -> Dict[str, Any]:
        """Synchronous wrapper for Celery tasks."""
        import asyncio
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(self.create_database(**kwargs))
        finally:
            loop.close()


# Singleton instance
coolify_client = CoolifyClient()
