import json
import asyncio
from channels.generic.websocket import AsyncWebsocketConsumer
from asgiref.sync import sync_to_async
# from kubernetes.stream import stream as k8s_stream # (Requires sync logic/threading, complex for AsyncConsumer)

class TerminalConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.deployment_id = self.scope['url_route']['kwargs']['deployment_id']
        # Verify user has access to this deployment (mock check)
        await self.accept()
        await self.send(text_data=json.dumps({
            'message': f'Connected to terminal for deployment {self.deployment_id}...\r\n$ '
        }))

    async def disconnect(self, close_code):
        pass

    async def receive(self, text_data):
        # Simulate shell response
        # In real implementation: pipe to k8s_stream(v1.connect_get_namespaced_pod_exec, ...)

        command = text_data

        if command == '\r':
            response = '\r\n$ '
        else:
            # Echo back
            response = command

            # Simple simulation
            if command.strip() == 'ls':
                response = '\r\nbin  boot  dev  etc  home  lib  media  mnt  opt  proc  root  run  sbin  srv  sys  tmp  usr  var\r\n$ '
            elif command.strip() == 'whoami':
                response = '\r\nroot\r\n$ '

        await self.send(text_data=json.dumps({'message': response}))
