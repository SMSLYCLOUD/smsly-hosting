import sys
import re

with open('backend/apps/deployments/consumers.py', 'r') as f:
    content = f.read()

# Add logging to connect
content = re.sub(r'async def connect\(self\):', r'async def connect(self):\n        logger.info("[CONSOLE_DEBUG] TerminalConsumer.connect() started for deployment %s", self.scope["url_route"]["kwargs"]["deployment_id"])', content)

content = re.sub(r'await self\.close\(code=4001\)', r'logger.error("[CONSOLE_DEBUG] Closing 4001: Missing token"); await self.close(code=4001)', content)
content = re.sub(r'await self\.close\(code=4002\)', r'logger.error("[CONSOLE_DEBUG] Closing 4002: Invalid token"); await self.close(code=4002)', content)
content = re.sub(r'await self\.close\(code=4003\)', r'logger.error("[CONSOLE_DEBUG] Closing 4003: Ownership failed"); await self.close(code=4003)', content)

# Add logging to _async_setup
content = re.sub(r'except Exception as e:\n(.*?)logger\.error\("Error during terminal setup', r'except Exception as e:\n\1logger.error("[CONSOLE_DEBUG] Error during terminal setup: %s", e, exc_info=True)', content)

# Add logging to receive
content = re.sub(r'async def receive\(self, text_data=None, bytes_data=None\):', r'async def receive(self, text_data=None, bytes_data=None):\n        logger.info("[CONSOLE_DEBUG] receive() called: text_data=%s, bytes_data=%s", bool(text_data), bool(bytes_data))', content)

content = re.sub(r'except Exception as e:\n(.*?)logger\.error\("Error forwarding input to container', r'except Exception as e:\n\1logger.error("[CONSOLE_DEBUG] Error forwarding input to container: %s", e, exc_info=True)', content)

# Add logging to _blocking_read
content = re.sub(r'def _blocking_read\(self\):', r'def _blocking_read(self):\n        logger.debug("[CONSOLE_DEBUG] _blocking_read() started")', content)

content = re.sub(r'except Exception as e:\n(.*?)err_name =', r'except Exception as e:\n            logger.error("[CONSOLE_DEBUG] _blocking_read exception: %s", e, exc_info=True)\n\1err_name =', content)

# Write back
with open('backend/apps/deployments/consumers.py', 'w') as f:
    f.write(content)
