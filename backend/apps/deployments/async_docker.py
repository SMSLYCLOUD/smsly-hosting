import asyncio
import os
import urllib.parse


class AsyncDockerExec:
    def __init__(self):
        docker_host = os.environ.get('DOCKER_HOST', 'unix:///var/run/docker.sock')
        self.is_unix = docker_host.startswith('unix://')
        if self.is_unix:
            self.socket_path = docker_host.replace('unix://', '')
        else:
            parsed = urllib.parse.urlparse(docker_host)
            self.host = parsed.hostname
            self.port = parsed.port or 2375

    async def connect(self):
        if self.is_unix:
            return await asyncio.open_unix_connection(self.socket_path)
        else:
            return await asyncio.open_connection(self.host, self.port)
